"""
ML-eligibility profiler -- Gate 1 of docs/ml_strategy_plan.md §3
("Deciding where ML applies: three gates, in this order").

Answers ONE narrow, deterministic question per numeric column: is there
enough clean, per-entity history here for a challenger baseline (SLOT E2,
datainsights/ml/baselines.py) to mean anything? This is a structural
check, not a relevance judgement -- Gate 2 (does a binding map this
column to a canonical measure) and Gate 3 (an LLM proposal for anything
still unmapped, onboarding/ml_measure_proposer.py) come after this.

Deliberately reuses onboarding/profiler.py's type/uniqueness/date
inference rather than re-deriving it -- this module only adds the
per-entity-history question profiling a single table's shape doesn't
answer. No LLM, no model fitting, no config writes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import pandas as pd

from onboarding.profiler import TableProfile

# R7: the floors live in config/ml_policy.yaml's `power_criteria:` block
# (datainsights/ml/policy.py::load_power_criteria) with their reasoning.
# These module constants are the fallbacks when no policy file exists.
DEFAULT_MIN_OBSERVATIONS = 8   # matches IsolationForestBaseline.min_observations
DEFAULT_MIN_ENTITIES = 30      # below this a robust-baseline comparison has no power
DEFAULT_MAX_NULL_RATE = 0.2

_ENTITY_COLUMN_PATTERN = re.compile(r"(_id|id)$", re.IGNORECASE)


@dataclass
class MeasureEligibility:
    table: str
    column: str
    eligible: bool                       # Gate 1a: a ROBUST-BASELINE comparison (SLOT E2) is meaningful
    reasons: list[str] = field(default_factory=list)
    entity_column: str | None = None
    n_entities: int = 0
    observations_per_entity: float | None = None
    # R7 -- Gate 1b: a CROSS-ENTITY ML challenger could be estimated AND
    # judged here. Stricter, and on every shipped dataset today: False,
    # with the criterion that failed named in ml_reasons.
    ml_challenger_eligible: bool = False
    ml_reasons: list[str] = field(default_factory=list)


def _entity_columns(profile: TableProfile, exclude: str) -> list[str]:
    """Columns that plausibly identify the ENTITY a measure repeats over
    (AGRMNT_ID, PRTY_ID, account_id, ...) -- named like a key but NOT
    unique in this table, since a per-row unique key can't group rows
    into a per-entity history. Order preserved from the profile (stable,
    not alphabetical) so the same column wins on repeated calls."""
    return [c.name for c in profile.columns
           if c.name != exclude and not c.is_unique and _ENTITY_COLUMN_PATTERN.search(c.name)]


def _assess_column(df: pd.DataFrame, profile: TableProfile, col_name: str, *,
                   min_observations: int, min_entities: int, max_null_rate: float,
                   ml_criteria=None, outcome_labels: int = 0) -> MeasureEligibility:
    col = next(c for c in profile.columns if c.name == col_name)
    reasons: list[str] = []

    if col.is_unique:
        reasons.append("column is a unique key/identifier, not a measure")

    series = df[col_name] if col_name in df.columns else pd.Series(dtype=float)
    null_rate = float(series.isna().mean()) if len(series) else 1.0
    if null_rate > max_null_rate:
        reasons.append(f"null rate {null_rate:.0%} exceeds the {max_null_rate:.0%} maximum")

    non_null = series.dropna()
    if len(non_null) and non_null.nunique() <= 1:
        reasons.append("column is constant (zero variance) -- cannot carry signal")

    if not profile.date_columns:
        reasons.append("table has no associated time column -- point-in-time correctness needs one")

    entity_cols = _entity_columns(profile, exclude=col_name)
    entity_col = entity_cols[0] if entity_cols else None
    n_entities = 0
    obs_per_entity: float | None = None
    if entity_col and entity_col in df.columns and col_name in df.columns:
        counts = df.groupby(entity_col)[col_name].apply(lambda s: int(s.notna().sum()))
        counts = counts[counts > 0]
        n_entities = int(len(counts))
        obs_per_entity = float(counts.mean()) if n_entities else 0.0
        if obs_per_entity < min_observations:
            reasons.append(
                f"average {obs_per_entity:.1f} observation(s) per entity, below the "
                f"{min_observations} a challenger baseline needs (IsolationForestBaseline's own floor)")
        if n_entities < min_entities:
            reasons.append(
                f"only {n_entities} entit{'y has' if n_entities == 1 else 'ies have'} any observation "
                f"of this column, below the {min_entities} minimum for a meaningful comparison")
    else:
        reasons.append(
            "no entity/grouping column found (expected a column named like '*_id' or '*ID' that "
            "repeats across rows) -- cannot assess whether history exists per entity")

    ml_reasons: list[str] = []
    if reasons:
        ml_reasons.append("not even robust-baseline eligible (see reasons)")
    if ml_criteria is not None:
        c = ml_criteria
        if n_entities < c.min_entities:
            ml_reasons.append(
                f"{n_entities} entities, below the {c.min_entities} a cross-entity model needs "
                f"(power_criteria.ml_challenger.min_entities)")
        needed = c.window_points * c.min_history_multiple_of_window
        if obs_per_entity is None or obs_per_entity < needed:
            ml_reasons.append(
                f"average {obs_per_entity or 0:.1f} observations per entity, below {needed} "
                f"({c.min_history_multiple_of_window}x the {c.window_points}-point window E2 already uses -- "
                f"below this the window IS the whole record)")
        if outcome_labels < c.min_outcome_labels:
            ml_reasons.append(
                f"{outcome_labels} RM outcome labels recorded, below the {c.min_outcome_labels} needed for an "
                f"evaluation protocol -- without labels a challenger can be shown different, never better")

    return MeasureEligibility(
        table=profile.physical_table, column=col_name, eligible=not reasons, reasons=reasons,
        entity_column=entity_col, n_entities=n_entities, observations_per_entity=obs_per_entity,
        ml_challenger_eligible=not ml_reasons, ml_reasons=ml_reasons,
    )


def ml_challenger_verdict(eligibility: dict[str, list[MeasureEligibility]]) -> tuple[bool, list[str]]:
    """R7: the one sentence the ML tab must say. (True, []) when at least
    one measure could support a cross-entity challenger; otherwise
    (False, reasons) where reasons are the criteria failed by the measure
    that came CLOSEST (fewest failures) -- so the user sees what the
    dataset would need, not a wall of every column's failures."""
    candidates = [c for cols in eligibility.values() for c in cols if c.eligible]
    if not candidates:
        return False, ["no measure is even robust-baseline eligible on this dataset"]
    if any(c.ml_challenger_eligible for c in candidates):
        return True, []
    closest = min(candidates, key=lambda c: len(c.ml_reasons))
    return False, [f"{closest.table}.{closest.column}: {r}" for r in closest.ml_reasons]


def assess(data_dir: str, profiles: dict[str, TableProfile], *,
          min_observations: int | None = None,
          min_entities: int | None = None,
          max_null_rate: float = DEFAULT_MAX_NULL_RATE,
          criteria=None, outcome_labels: int | None = None) -> dict[str, list[MeasureEligibility]]:
    """One MeasureEligibility per numeric (int/float) column per table.
    Non-numeric columns are never assessed -- a baseline models a
    magnitude, so a categorical/string column is out of scope by
    construction, not by a rejection reason. `data_dir` is re-read
    (bounded, same 5000-row sample onboarding/profiler.py already reads)
    because TableProfile itself doesn't retain the raw frame."""
    # R7: thresholds come from config/ml_policy.yaml's power_criteria
    # unless a caller overrides them explicitly (tests do).
    from datainsights.ml.policy import load_power_criteria, outcome_label_count

    criteria = criteria or load_power_criteria()
    min_observations = criteria.robust_baseline.min_observations_per_entity if min_observations is None else min_observations
    min_entities = criteria.robust_baseline.min_entities if min_entities is None else min_entities
    outcome_labels = outcome_label_count() if outcome_labels is None else outcome_labels

    results: dict[str, list[MeasureEligibility]] = {}
    for table_name, profile in profiles.items():
        path = os.path.join(data_dir, f"{table_name}.csv")
        if not os.path.exists(path):
            continue
        numeric_cols = [c.name for c in profile.columns if c.inferred_type in ("int", "float")]
        if not numeric_cols:
            continue
        # R7: entity COUNT must be exact, not sampled -- the old 5000-row
        # sample saw 39 of FDM's 92 deposit accounts and would have failed
        # a population criterion the data actually meets. Still bounded:
        # only the numeric measures and the entity-like key columns are
        # read, never the whole table's width.
        wanted = set(numeric_cols) | {c.name for c in profile.columns if _ENTITY_COLUMN_PATTERN.search(c.name)}
        df = pd.read_csv(path, usecols=lambda c: c in wanted)
        table_results = [
            _assess_column(df, profile, col_name, min_observations=min_observations,
                          min_entities=min_entities, max_null_rate=max_null_rate,
                          ml_criteria=criteria.ml_challenger, outcome_labels=outcome_labels)
            for col_name in numeric_cols
        ]
        results[table_name] = table_results
    return results
