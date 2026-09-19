"""
Point-in-time feature layer (docs/generalization_plan.md Phase 4) -- ONE
PIT-safe windowed aggregation, tested once, rather than each detector
(and any future consumer -- SLOT E4's label pipeline, Phase 5's
onboarding tool) reimplementing its own future-leakage guard.

Deliberately NOT a detector migration: the 9 existing detectors in
detection_engine/ already implement their own correct as-of filtering,
each independently proven by its own "no future leakage" test -- ripping
that out and rewiring 9 files through a shared layer is real, separate,
higher-risk work with no behavioural upside (they're already correct)
and is not attempted here. This module is the shared primitive available
to detectors and other consumers going forward, not a retrofit of what
already works.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

import pandas as pd

Aggregation = Literal["mean", "median", "sum", "last", "count", "first"]


def compute(series: pd.DataFrame, *, as_of: date, date_col: str, value_col: str,
           window_days: int | None = None, agg: Aggregation = "last") -> float | None:
    """One windowed, as-of-safe aggregation over `series[value_col]`.

    Rows with `date_col` > `as_of` are excluded unconditionally, before
    anything else -- this is the one thing every caller needs guaranteed
    regardless of which aggregation they ask for. `window_days=None`
    means "everything up to and including as_of"; otherwise only rows
    within `[as_of - window_days, as_of]`.

    Returns None (never a fabricated 0 or NaN treated as a real value)
    if no rows fall in the window -- "insufficient evidence" is a state
    every caller must handle explicitly, the same discipline
    detection_engine's own detectors already hold themselves to."""
    if series.empty:
        return None
    ts = pd.to_datetime(series[date_col])
    as_of_ts = pd.Timestamp(as_of)
    mask = ts <= as_of_ts
    if window_days is not None:
        mask &= ts >= pd.Timestamp(as_of - timedelta(days=window_days))
    windowed = series.loc[mask, value_col]
    if windowed.empty:
        return None

    if agg == "mean":
        return float(windowed.mean())
    if agg == "median":
        return float(windowed.median())
    if agg == "sum":
        return float(windowed.sum())
    if agg == "count":
        return float(len(windowed))
    if agg == "last":
        # last BY DATE, not by row order -- a caller may hand this an
        # unsorted frame.
        return float(series.loc[mask].assign(_ts=ts[mask]).sort_values("_ts")[value_col].iloc[-1])
    if agg == "first":
        return float(series.loc[mask].assign(_ts=ts[mask]).sort_values("_ts")[value_col].iloc[0])
    raise ValueError(f"unknown aggregation: {agg!r}")


def compute_many(source, entity: str, *, group_col: str, date_col: str, value_col: str,
                 as_of: date, window_days: int | None = None, agg: Aggregation = "last") -> pd.DataFrame:
    """T6 (docs/ml_strategy_plan.md §7/§9) -- the SAME windowed,
    as-of-safe aggregation as compute() above, but for the WHOLE BOOK at
    once: one row per `group_col` value, columns [group_col,
    f'{value_col}_{agg}']. Pushes down to the source
    (`source.aggregate()`, real SQL executed by DuckDB today) when
    `source.capabilities().supports_aggregate_pushdown` is true;
    otherwise pulls the whole table once and computes the IDENTICAL
    result in pandas via a groupby over compute() -- the two paths are
    golden-tested to return the same values
    (tests/test_feature_pushdown.py), so choosing one is a performance
    decision, never a behavioural one.

    NOT wired for FdmLocalSource's bi-temporal tables -- proven for
    OfflineLocalSource's flat schema (the `legacy`/`sba` bindings) only.
    The bi-temporal as-at collapse CanonicalSource/FdmLocalSource do adds
    real complexity a SQL pushdown would need to replicate exactly;
    real, disclosed, undone work (docs/gap_analysis.md)."""
    caps = source.capabilities()
    aggregate_fn = getattr(source, "aggregate", None)
    if caps.supports_aggregate_pushdown and aggregate_fn is not None:
        return aggregate_fn(entity, group_col=group_col, value_col=value_col,
                            date_col=date_col, agg=agg, as_of=as_of, window_days=window_days)

    df, _ = source.read_entity(entity, allow_unbounded=True)
    out_col = f"{value_col}_{agg}"
    rows = []
    for group_value, grp in df.groupby(group_col):
        value = compute(grp, as_of=as_of, date_col=date_col, value_col=value_col,
                        window_days=window_days, agg=agg)
        if value is not None:
            rows.append({group_col: group_value, out_col: value})
    return pd.DataFrame(rows, columns=[group_col, out_col])
