"""
Per-schema ML policy -- T2 (docs/ml_strategy_plan.md §4/§9). Loads
config/ml_policy.yaml and resolves, for one canonical measure, which
algorithm (if any) should challenge the deterministic default -- with the
precedence a user actually needs: an explicit human decision always wins,
a structurally-eligible canonical measure is enabled by default, and an
LLM proposal is never trusted until a human has separately accepted it
(mirrors onboarding/binding_proposer.py's own discipline).

This module makes NO decision about eligibility itself -- that's
onboarding/ml_profiler.py (Gate 1) and a binding (Gate 2). It only
resolves what config/ml_policy.yaml says once those are known.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_POLICY_PATH = REPO_ROOT / "config" / "ml_policy.yaml"

# The only algorithms datainsights/ml/runner.py can actually run today.
# Kept here (not just in the runner) so a policy file with a typo fails
# at LOAD time, not silently at run time.
VALID_ALGORITHMS = ("deterministic", "isolation_forest")


class MlPolicyError(Exception):
    pass


class MeasurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    algorithm: str | None = None
    hyperparameters: dict = {}
    chosen_by: str = "policy"
    disabled_reason: str | None = None

    @field_validator("algorithm")
    @classmethod
    def _algorithm_is_registered(cls, v):
        if v is not None and v not in VALID_ALGORITHMS:
            raise ValueError(
                f"unknown algorithm {v!r} -- must be one of {VALID_ALGORITHMS}. "
                f"A challenger has to be built into datainsights/ml/runner.py's "
                f"ALGORITHMS registry before a policy file can select it."
            )
        return v


class SchemaDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: str = "deterministic"
    require_challenger_win: bool = True

    @field_validator("algorithm")
    @classmethod
    def _algorithm_is_registered(cls, v):
        if v not in VALID_ALGORITHMS:
            raise ValueError(f"unknown default algorithm {v!r} -- must be one of {VALID_ALGORITHMS}")
        return v


class RobustBaselineCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_observations_per_entity: int = 8
    min_entities: int = 30


class MlChallengerCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_entities: int = 200
    window_points: int = 30
    min_history_multiple_of_window: int = 2
    min_outcome_labels: int = 100


class PowerCriteria(BaseModel):
    """R7: the stated criteria under which a challenger is eligible at
    all -- see the `power_criteria:` block in config/ml_policy.yaml for
    the reasoning behind each number."""
    model_config = ConfigDict(extra="forbid")
    robust_baseline: RobustBaselineCriteria = RobustBaselineCriteria()
    ml_challenger: MlChallengerCriteria = MlChallengerCriteria()


def load_power_criteria(*, path: str | os.PathLike | None = None) -> PowerCriteria:
    policy_path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not policy_path.exists():
        return PowerCriteria()
    with open(policy_path) as f:
        raw = yaml.safe_load(f) or {}
    try:
        return PowerCriteria.model_validate(raw.get("power_criteria") or {})
    except Exception as e:  # noqa: BLE001
        raise MlPolicyError(f"invalid power_criteria in {policy_path}: {e}") from e


def outcome_label_count(feedback_db_path: str | os.PathLike | None = None) -> int:
    """How many RM responses (datainsights/rm_feedback.py, the D6 label
    source) exist -- the evaluation-protocol half of the ML gate. Zero
    when the store does not exist yet."""
    import sqlite3

    db = Path(feedback_db_path) if feedback_db_path is not None else REPO_ROOT / "var" / "rm_feedback.db"
    if not db.exists():
        return 0
    try:
        with sqlite3.connect(db) as con:
            return int(con.execute("SELECT count(*) FROM rm_feedback").fetchone()[0])
    except sqlite3.Error:
        return 0


class SchemaPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measures: dict[str, MeasurePolicy] = {}
    defaults: SchemaDefaults = SchemaDefaults()


@dataclass
class ResolvedMeasure:
    measure: str
    enabled: bool
    algorithm: str
    hyperparameters: dict = field(default_factory=dict)
    chosen_by: str = "none"          # "policy" | "gate1+binding" | "llm_proposal" | "none"
    reason: str | None = None        # why disabled, or why an llm_proposal isn't live yet


@dataclass
class MlPolicy:
    schema_name: str
    measures: dict[str, MeasurePolicy]
    defaults: SchemaDefaults

    def resolve(self, measure: str, *, gate1_eligible: bool = False,
               mapped_by_binding: bool = False, llm_proposed: bool = False) -> ResolvedMeasure:
        """Precedence, highest first:
        1. An explicit entry in config/ml_policy.yaml for this measure.
        2. Gate 1 (structurally eligible) AND Gate 2 (the active binding
           maps this measure) -- enabled with the schema's default algorithm.
        3. Gate 3 -- an LLM proposed this as an extra measure, but no
           human has accepted it into policy yet -- stays disabled, with
           that stated as the reason (never silently enabled).
        4. Nothing applies: disabled, no reason to enable it."""
        explicit = self.measures.get(measure)
        if explicit is not None:
            return ResolvedMeasure(
                measure=measure, enabled=explicit.enabled,
                algorithm=explicit.algorithm or self.defaults.algorithm,
                hyperparameters=explicit.hyperparameters, chosen_by="policy",
                reason=explicit.disabled_reason if not explicit.enabled else None,
            )
        if gate1_eligible and mapped_by_binding:
            return ResolvedMeasure(
                measure=measure, enabled=True, algorithm=self.defaults.algorithm,
                hyperparameters={}, chosen_by="gate1+binding", reason=None,
            )
        if llm_proposed:
            return ResolvedMeasure(
                measure=measure, enabled=False, algorithm=self.defaults.algorithm,
                hyperparameters={}, chosen_by="llm_proposal",
                reason="an LLM proposed this measure but no human has accepted it into "
                       "config/ml_policy.yaml yet -- see onboarding/ml_measure_proposer.py",
            )
        return ResolvedMeasure(
            measure=measure, enabled=False, algorithm=self.defaults.algorithm,
            hyperparameters={}, chosen_by="none",
            reason="not structurally eligible (Gate 1) and not mapped by the active binding (Gate 2)",
        )


def load_policy(schema: str, *, path: str | os.PathLike | None = None) -> MlPolicy:
    """No policy file, or no entry for this schema: NOT an error -- returns
    an empty policy (defaults only), so gate1+binding resolution still
    works with zero configuration for the common case
    (docs/ml_strategy_plan.md's "no per-schema ML configuration required
    for the common case")."""
    policy_path = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not policy_path.exists():
        return MlPolicy(schema_name=schema, measures={}, defaults=SchemaDefaults())

    with open(policy_path) as f:
        raw = yaml.safe_load(f) or {}

    schema_raw = (raw.get("schemas") or {}).get(schema)
    if schema_raw is None:
        return MlPolicy(schema_name=schema, measures={}, defaults=SchemaDefaults())

    try:
        parsed = SchemaPolicy.model_validate(schema_raw)
    except Exception as e:  # noqa: BLE001 -- re-raise typed, with the schema name in context
        raise MlPolicyError(f"invalid ml policy for schema {schema!r} in {policy_path}: {e}") from e

    return MlPolicy(schema_name=schema, measures=parsed.measures, defaults=parsed.defaults)
