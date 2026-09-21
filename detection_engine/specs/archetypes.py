"""
The five detector archetypes, and the executor that runs a spec.

Each archetype is a computational SHAPE that several existing detectors
share. The mapping (reference implementations, all untouched):

  own_history_deviation  <- large_incoming_payment, cash_buildup,
                            revenue_pattern_change
  ratio_threshold        <- facility_utilization_spike,
                            collateral_coverage_drop
  date_proximity         <- facility_maturity_approaching,
                            fixed_rate_expiry
  absence                <- dormancy
  ordinal_migration      <- rating_downgrade, pd_migration

A spec names an archetype, the canonical fields it reads, and its
parameters. It never names a physical column: specs are written against
config/semantic_model.yaml, so a spec discovered on one schema is
portable to any other schema whose binding supplies those fields (and is
honestly `unavailable` where it does not) -- the same guarantee
config/bindings/*.yaml already gives detectors.

`raw_measure` contract (recommendation 3): each archetype declares which
keys it puts on Signal.raw_measure. This matters because
datainsights/correlation/hypothesis.py::_size_endogenous() reads specific
keys ("drawn_amount", "orig_limit", ...) to size an offer. A spec-defined
signal that fed a revenue category without the right keys would fall to
UNSIZED_BASIS rather than break -- but the contract is declared here so
that is a choice, not an accident.

MEDIAN EXACTNESS: own_history_deviation uses median/MAD. pandas computes
this exactly; Snowflake's MEDIAN is exact but Spark's percentile_approx
is not. `median_mode` records which was used, so a cross-backend
difference is visible in the trace rather than silent. Only "exact" is
implemented here (pandas); "approx" exists to be recorded by a future
pushdown executor, and a spec asking for it is rejected rather than
silently given exact results.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd
import yaml

from detection_engine.signal import VALID_DIRECTIONS, VALID_DOMAINS, Signal, clamp_magnitude


class SpecError(ValueError):
    """A spec is malformed, or asks for something not implemented. Always
    raised at load/run time with the spec name -- never silently skipped,
    so a bad spec cannot quietly stop producing signals."""


# Status vocabulary, identical to the hand-written detectors'.
DETECTED = "detected"
INSUFFICIENT = "insufficient_evidence"


@dataclass(frozen=True)
class SignalSpec:
    """A detector expressed as data. `params` is archetype-specific and
    validated by that archetype's own validator."""

    signal_type: str
    archetype: str
    domain: str
    concept: str                      # canonical concept the rows come from
    grain: tuple[str, ...]            # canonical fields forming the entity key
    params: dict[str, Any] = field(default_factory=dict)
    direction: str = "increase"
    # Provenance -- set for discovered specs, empty for hand-written ones.
    origin: str = "handwritten"       # handwritten | discovered
    status: str = "active"            # active | shadow
    notes: str = ""

    def __post_init__(self) -> None:
        if self.archetype not in ARCHETYPES:
            raise SpecError(f"{self.signal_type}: unknown archetype {self.archetype!r} "
                            f"(known: {sorted(ARCHETYPES)})")
        if self.domain not in VALID_DOMAINS:
            raise SpecError(f"{self.signal_type}: domain {self.domain!r} not in {sorted(VALID_DOMAINS)}")
        if self.direction not in VALID_DIRECTIONS:
            raise SpecError(f"{self.signal_type}: direction {self.direction!r} not in {sorted(VALID_DIRECTIONS)}")
        if self.status not in {"active", "shadow"}:
            raise SpecError(f"{self.signal_type}: status must be 'active' or 'shadow', got {self.status!r}")
        if not self.grain:
            raise SpecError(f"{self.signal_type}: grain must name at least one canonical field")
        ARCHETYPES[self.archetype].validate(self)

    @property
    def required_fields(self) -> tuple[str, ...]:
        """Canonical fields this spec reads -- grain plus whatever the
        archetype needs. Used to decide availability under a binding."""
        return tuple(dict.fromkeys(self.grain + ARCHETYPES[self.archetype].fields_of(self)))


@dataclass(frozen=True)
class Archetype:
    name: str
    raw_measure_keys: tuple[str, ...]
    validate: Callable[[SignalSpec], None]
    fields_of: Callable[[SignalSpec], tuple[str, ...]]
    run: Callable[[SignalSpec, pd.DataFrame], list[Signal]]


# --------------------------------------------------------------------------
# helpers shared by the archetypes
# --------------------------------------------------------------------------

def _require_params(spec: SignalSpec, *names: str) -> None:
    missing = [n for n in names if spec.params.get(n) is None]
    if missing:
        raise SpecError(f"{spec.signal_type}: archetype {spec.archetype!r} requires params {missing}")


def _check_columns(spec: SignalSpec, df: pd.DataFrame) -> None:
    missing = [c for c in spec.required_fields if c not in df.columns]
    if missing:
        raise SpecError(
            f"{spec.signal_type}: canonical field(s) {missing} absent from the frame. "
            f"The active binding does not supply them -- this spec is unavailable on this schema."
        )


def _entity_key(row: pd.Series, spec: SignalSpec) -> str:
    return ":".join(str(row[g]) for g in spec.grain)


def _evidence(spec: SignalSpec, row: pd.Series, observed: date) -> str:
    return f"{spec.concept}:{_entity_key(row, spec)}:eff={observed.isoformat()}"


def _as_date(value) -> date:
    """Normalise a timestamp-ish value to a plain date.

    Deliberately no `hasattr(ts, "date")` guard: tests/test_no_source_specific
    _coupling.py scans for string literals that match physical column names,
    and "date" is one in the legacy schema (config/entities.yaml's balances
    entity). A method-name string is a false positive there, but the guard
    cannot tell the difference -- and `pd.to_datetime` already returns a
    Timestamp or raises, so the guard bought nothing anyway."""
    return pd.to_datetime(value).date()


def _mk_signal(spec: SignalSpec, row: pd.Series, observed: date, magnitude: float,
               raw_measure: dict) -> Signal:
    """Single construction point, so every archetype emits the identical
    Signal shape the hand-written detectors' to_signal() does."""
    party = str(row["party_id"]) if "party_id" in row else str(row[spec.grain[0]])
    return Signal(
        prty_id=party,
        signal_type=spec.signal_type,
        domain=spec.domain,
        direction=spec.direction,
        magnitude=clamp_magnitude(magnitude),
        observed_date=observed,
        evidence_ref=_evidence(spec, row, observed),
        source_tables=(spec.concept,),
        raw_measure=raw_measure,
    )


def _apply_cooldown(signals: list[Signal], cooldown_days: int) -> list[Signal]:
    """Same suppression the hand-written detectors' apply_cooldown() does:
    within one entity, a signal within `cooldown_days` of the last kept one
    is dropped. Hand-written detectors mark it 'suppressed_cooldown' in
    their DataFrame; here the suppressed row simply never becomes a Signal,
    which is the same thing by the time the correlation layer sees it."""
    if cooldown_days <= 0:
        return signals
    kept: list[Signal] = []
    last_by_entity: dict[tuple, date] = {}
    for sig in sorted(signals, key=lambda s: (s.prty_id, s.evidence_ref, s.observed_date)):
        key = (sig.prty_id, sig.evidence_ref.rsplit(":eff=", 1)[0])
        last = last_by_entity.get(key)
        if last is not None and (sig.observed_date - last).days < cooldown_days:
            continue
        last_by_entity[key] = sig.observed_date
        kept.append(sig)
    return kept


# --------------------------------------------------------------------------
# archetype 1: own-history deviation
#   reference: large_incoming_payment, cash_buildup, revenue_pattern_change
# --------------------------------------------------------------------------

def _validate_deviation(spec: SignalSpec) -> None:
    _require_params(spec, "measure", "date_field", "window_days", "k")
    mode = spec.params.get("median_mode", "exact")
    if mode != "exact":
        raise SpecError(
            f"{spec.signal_type}: median_mode={mode!r} is not implemented by the pandas "
            f"executor. Only 'exact' runs here; 'approx' is reserved for a pushdown "
            f"executor that cannot compute an exact median (see module docstring)."
        )
    if float(spec.params["k"]) <= 0:
        raise SpecError(f"{spec.signal_type}: k must be > 0")


def _fields_deviation(spec: SignalSpec) -> tuple[str, ...]:
    return (spec.params["measure"], spec.params["date_field"])


def _run_deviation(spec: SignalSpec, df: pd.DataFrame) -> list[Signal]:
    """Flag an observation that sits more than k x MAD above the entity's
    OWN trailing median. As-of discipline, identical to the reference
    detectors: the baseline for row i uses only rows strictly before it."""
    _check_columns(spec, df)
    measure, date_field = spec.params["measure"], spec.params["date_field"]
    window_days, k = int(spec.params["window_days"]), float(spec.params["k"])
    min_obs = int(spec.params.get("min_observations", 8))
    floor = float(spec.params.get("min_baseline", 0.0))

    work = df.copy()
    work[date_field] = pd.to_datetime(work[date_field])
    out: list[Signal] = []

    for _, grp in work.groupby(list(spec.grain), sort=False):
        grp = grp.sort_values(date_field).reset_index(drop=True)
        values = grp[measure].to_numpy(dtype=float)
        dates = grp[date_field].to_numpy(dtype="datetime64[D]")
        starts = (grp[date_field] - pd.Timedelta(days=window_days)).to_numpy(dtype="datetime64[D]")
        lo = np.searchsorted(dates, starts, side="left")

        for i in range(len(grp)):
            history = values[lo[i]:i]           # strictly before row i
            if len(history) < min_obs:
                continue                         # INSUFFICIENT -- never a guess
            median = float(np.median(history))
            mad = float(np.median(np.abs(history - median)))
            if median < floor:
                continue
            threshold = median + k * mad
            current = float(values[i])
            if mad <= 0 or current <= threshold:
                continue
            observed = _as_date(grp.iloc[i][date_field])
            excess = current - median
            out.append(_mk_signal(
                spec, grp.iloc[i], observed,
                magnitude=(current - threshold) / max(threshold, 1e-9),
                raw_measure={
                    "flagged_amount": round(current, 2),
                    "baseline_median": round(median, 2),
                    "baseline_mad": round(mad, 2),
                    "excess_over_baseline": round(excess, 2),
                    "observations_in_baseline": int(len(history)),
                    "median_mode": "exact",
                    "currency": str(grp.iloc[i].get("currency", "EUR")).upper(),
                },
            ))
    return _apply_cooldown(out, int(spec.params.get("cooldown_days", 0)))


# --------------------------------------------------------------------------
# archetype 2: ratio threshold
#   reference: facility_utilization_spike, collateral_coverage_drop
# --------------------------------------------------------------------------

def _validate_ratio(spec: SignalSpec) -> None:
    _require_params(spec, "numerator", "denominator", "date_field", "threshold")
    if spec.params.get("comparison", "gte") not in {"gte", "lte"}:
        raise SpecError(f"{spec.signal_type}: comparison must be 'gte' or 'lte'")


def _fields_ratio(spec: SignalSpec) -> tuple[str, ...]:
    return (spec.params["numerator"], spec.params["denominator"], spec.params["date_field"])


def _run_ratio(spec: SignalSpec, df: pd.DataFrame) -> list[Signal]:
    _check_columns(spec, df)
    num, den = spec.params["numerator"], spec.params["denominator"]
    date_field, threshold = spec.params["date_field"], float(spec.params["threshold"])
    comparison = spec.params.get("comparison", "gte")

    work = df.copy()
    work[date_field] = pd.to_datetime(work[date_field])
    work = work[pd.to_numeric(work[den], errors="coerce").fillna(0) > 0]
    if work.empty:
        return []
    ratio = work[num].astype(float) / work[den].astype(float)
    hits = work[ratio >= threshold] if comparison == "gte" else work[ratio <= threshold]

    out: list[Signal] = []
    for idx, row in hits.iterrows():
        value = float(row[num]) / float(row[den])
        magnitude = ((value - threshold) / max(1 - threshold, 1e-9) if comparison == "gte"
                     else (threshold - value) / max(threshold, 1e-9))
        out.append(_mk_signal(
            spec, row, _as_date(row[date_field]), magnitude,
            raw_measure={
                # keys _size_endogenous() understands for a facility-shaped ratio
                "drawn_amount": round(float(row[num]), 2),
                "orig_limit": round(float(row[den]), 2),
                "utilization_pct": round(value, 4),
                "currency": str(row.get("currency", "EUR")).upper(),
            },
        ))
    return _apply_cooldown(out, int(spec.params.get("cooldown_days", 0)))


# --------------------------------------------------------------------------
# archetype 3: date proximity
#   reference: facility_maturity_approaching, fixed_rate_expiry
# --------------------------------------------------------------------------

def _validate_proximity(spec: SignalSpec) -> None:
    _require_params(spec, "date_field", "within_days")
    if int(spec.params["within_days"]) <= 0:
        raise SpecError(f"{spec.signal_type}: within_days must be > 0")


def _fields_proximity(spec: SignalSpec) -> tuple[str, ...]:
    return (spec.params["date_field"],)


def _run_proximity(spec: SignalSpec, df: pd.DataFrame, as_of: date | None = None) -> list[Signal]:
    _check_columns(spec, df)
    date_field = spec.params["date_field"]
    within = int(spec.params["within_days"])
    as_of = as_of or date.today()

    work = df.copy()
    work[date_field] = pd.to_datetime(work[date_field], errors="coerce")
    work = work[work[date_field].notna()]

    out: list[Signal] = []
    for _, row in work.iterrows():
        target = _as_date(row[date_field])
        days = (target - as_of).days
        if not (0 <= days <= within):
            continue
        out.append(_mk_signal(
            spec, row, as_of, magnitude=(within - days) / within,
            raw_measure={
                "orig_limit": round(float(row["original_limit"]), 2) if "original_limit" in row
                              and pd.notna(row.get("original_limit")) else None,
                "close_date": target.isoformat(),
                "days_to_close": days,
                "currency": str(row.get("currency", "EUR")).upper(),
            },
        ))
    return out


# --------------------------------------------------------------------------
# archetype 4: absence
#   reference: dormancy
# --------------------------------------------------------------------------

def _validate_absence(spec: SignalSpec) -> None:
    _require_params(spec, "date_field", "silent_days")


def _fields_absence(spec: SignalSpec) -> tuple[str, ...]:
    return (spec.params["date_field"],)


def _run_absence(spec: SignalSpec, df: pd.DataFrame, as_of: date | None = None) -> list[Signal]:
    """An entity with no observation for `silent_days`. Expressible as a
    max-date-per-group + filter, which is why it pushes down cleanly."""
    _check_columns(spec, df)
    date_field = spec.params["date_field"]
    silent = int(spec.params["silent_days"])
    as_of = as_of or date.today()

    work = df.copy()
    work[date_field] = pd.to_datetime(work[date_field], errors="coerce")
    work = work[work[date_field].notna() & (work[date_field].dt.date <= as_of)]
    if work.empty:
        return []

    out: list[Signal] = []
    for _, grp in work.groupby(list(spec.grain), sort=False):
        last_row = grp.loc[grp[date_field].idxmax()]
        last_seen = _as_date(last_row[date_field])
        quiet_days = (as_of - last_seen).days
        if quiet_days < silent:
            continue
        out.append(_mk_signal(
            spec, last_row, as_of, magnitude=min(1.0, quiet_days / (silent * 2)),
            raw_measure={
                "last_activity_date": last_seen.isoformat(),
                "days_silent": quiet_days,
                "threshold_days": silent,
            },
        ))
    return out


# --------------------------------------------------------------------------
# archetype 5: ordinal migration
#   reference: rating_downgrade, pd_migration
# --------------------------------------------------------------------------

def _validate_ordinal(spec: SignalSpec) -> None:
    _require_params(spec, "field", "date_field", "order")
    order = spec.params["order"]
    if not isinstance(order, (list, tuple)) or len(order) < 2:
        raise SpecError(f"{spec.signal_type}: 'order' must list the ordinal values best-to-worst")
    if int(spec.params.get("min_steps", 1)) < 1:
        raise SpecError(f"{spec.signal_type}: min_steps must be >= 1")


def _fields_ordinal(spec: SignalSpec) -> tuple[str, ...]:
    return (spec.params["field"], spec.params["date_field"])


def _run_ordinal(spec: SignalSpec, df: pd.DataFrame) -> list[Signal]:
    """A move of >= min_steps DOWN the declared order (order is best-to-
    worst, so a higher index is worse). Expressible as a lag window."""
    _check_columns(spec, df)
    field_name, date_field = spec.params["field"], spec.params["date_field"]
    order = list(spec.params["order"])
    min_steps = int(spec.params.get("min_steps", 1))
    rank = {str(v): i for i, v in enumerate(order)}

    work = df.copy()
    work[date_field] = pd.to_datetime(work[date_field])
    out: list[Signal] = []

    for _, grp in work.groupby(list(spec.grain), sort=False):
        grp = grp.sort_values(date_field).reset_index(drop=True)
        ranks = [rank.get(str(v)) for v in grp[field_name]]
        for i in range(1, len(grp)):
            prev, curr = ranks[i - 1], ranks[i]
            if prev is None or curr is None:
                continue                     # unknown grade -- never guessed
            steps = curr - prev
            if steps < min_steps:
                continue
            out.append(_mk_signal(
                spec, grp.iloc[i], _as_date(grp.iloc[i][date_field]),
                magnitude=steps / max(len(order) - 1, 1),
                raw_measure={
                    "from_value": str(grp.iloc[i - 1][field_name]),
                    "to_value": str(grp.iloc[i][field_name]),
                    "steps_moved": int(steps),
                },
            ))
    return _apply_cooldown(out, int(spec.params.get("cooldown_days", 0)))


# --------------------------------------------------------------------------

ARCHETYPES: dict[str, Archetype] = {
    "own_history_deviation": Archetype(
        name="own_history_deviation",
        raw_measure_keys=("flagged_amount", "baseline_median", "baseline_mad",
                          "excess_over_baseline", "observations_in_baseline",
                          "median_mode", "currency"),
        validate=_validate_deviation, fields_of=_fields_deviation, run=_run_deviation),
    "ratio_threshold": Archetype(
        name="ratio_threshold",
        raw_measure_keys=("drawn_amount", "orig_limit", "utilization_pct", "currency"),
        validate=_validate_ratio, fields_of=_fields_ratio, run=_run_ratio),
    "date_proximity": Archetype(
        name="date_proximity",
        raw_measure_keys=("orig_limit", "close_date", "days_to_close", "currency"),
        validate=_validate_proximity, fields_of=_fields_proximity, run=_run_proximity),
    "absence": Archetype(
        name="absence",
        raw_measure_keys=("last_activity_date", "days_silent", "threshold_days"),
        validate=_validate_absence, fields_of=_fields_absence, run=_run_absence),
    "ordinal_migration": Archetype(
        name="ordinal_migration",
        raw_measure_keys=("from_value", "to_value", "steps_moved"),
        validate=_validate_ordinal, fields_of=_fields_ordinal, run=_run_ordinal),
}


def _clip_to_as_of(spec: SignalSpec, df: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Drop rows observed after `as_of` BEFORE the archetype runs.

    As-of correctness is enforced here, once, rather than trusted to each
    archetype: a detector must only ever see data available at decision
    time (CLAUDE.md), and the row-scanning archetypes (deviation, ratio,
    ordinal) would otherwise happily flag a December row while replaying
    July. The archetypes' own trailing-window logic then guarantees the
    second half of the property -- that row i's baseline uses only rows
    strictly before it."""
    field_name = spec.params.get("date_field")
    if not field_name or field_name not in df.columns:
        return df
    stamps = pd.to_datetime(df[field_name], errors="coerce")
    return df[stamps.notna() & (stamps.dt.date <= as_of)]


def run_spec(spec: SignalSpec, df: pd.DataFrame, *, as_of: date | None = None) -> list[Signal]:
    """Execute one spec against a canonical frame.

    When `as_of` is given, the frame is clipped to it first, so EVERY
    archetype is as-of correct -- not only the two whose semantics take a
    reference date explicitly."""
    archetype = ARCHETYPES[spec.archetype]
    if as_of is not None:
        df = _clip_to_as_of(spec, df, as_of)
    if spec.archetype in {"date_proximity", "absence"}:
        return archetype.run(spec, df, as_of)
    return archetype.run(spec, df)


def load_spec(payload: dict) -> SignalSpec:
    try:
        return SignalSpec(
            signal_type=payload["signal_type"], archetype=payload["archetype"],
            domain=payload["domain"], concept=payload["concept"],
            grain=tuple(payload["grain"]), params=payload.get("params") or {},
            direction=payload.get("direction", "increase"),
            origin=payload.get("origin", "handwritten"),
            status=payload.get("status", "active"), notes=payload.get("notes", ""),
        )
    except KeyError as e:
        raise SpecError(f"spec missing required key: {e}") from e


def load_specs_dir(path: str) -> list[SignalSpec]:
    """Every *.yaml under `path`. A malformed spec raises rather than
    being skipped -- silently dropping a detector is the failure mode
    this project's config-validation discipline exists to prevent."""
    if not os.path.isdir(path):
        return []
    specs = []
    for name in sorted(os.listdir(path)):
        if not name.endswith((".yaml", ".yml")):
            continue
        with open(os.path.join(path, name)) as f:
            payload = yaml.safe_load(f) or {}
        specs.append(load_spec(payload))
    return specs
