"""
Gate 3 of docs/ml_strategy_plan.md §3 -- an LLM proposal for measures
that passed Gate 1 (onboarding/ml_profiler.py, structurally eligible)
but have no canonical concept in config/semantic_model.yaml to map onto,
so Gate 2 (the binding) cannot pick them up automatically. This is the
COLD-START case only: a schema-specific measure like
`covenant_headroom_pct` that the platform's canonical model has never
seen before.

Structurally identical to onboarding/binding_proposer.py, on purpose --
same validate-or-reject discipline, same isolation (one small call per
candidate, so one bad proposal never affects another), same rule: every
proposed table/column must be real, or the proposal is dropped and
logged in `rejected`, never silently kept. This is the SECOND agent in
this repo whose proposal, once accepted, becomes pipeline configuration
(config/ml_policy.yaml) rather than narration text -- which is exactly
why acceptance stays a separate, human, explicit step
(onboarding/accept.py's sibling for ML policy is the dashboard's
"Save policy" action, never automatic).

Once a challenger model has actually been fit for a measure, permutation
importance on that fitted model supersedes this LLM's guess permanently
-- see docs/ml_strategy_plan.md §3, Gate 3's closing note. This module
never re-runs once a measure has empirical results; it exists only to
seed the cold start.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from onboarding.ml_profiler import MeasureEligibility
from onboarding.profiler import TableProfile


class _ProposedMeasure(BaseModel):
    is_relevant: bool          # the model's own yes/no -- may legitimately say no
    proposed_name: str         # a short snake_case canonical-style name, e.g. "covenant_headroom_pct"
    business_meaning: str      # one sentence: why an RM/credit analyst would care
    confidence: float          # 0..1, the model's own estimate
    caveats: str                # anything that makes this measure risky to challenge (e.g. sparse, or already-normalised)


@dataclass
class MeasureProposal:
    table: str
    column: str
    accepted: bool
    proposed_name: str | None = None
    business_meaning: str = ""
    confidence: float = 0.0
    caveats: str = ""
    rejected_reason: str | None = None  # non-empty ONLY when accepted is False


def _propose_one(candidate: MeasureEligibility, profile: TableProfile, model) -> MeasureProposal:
    from strands import Agent

    col_profile = next((c for c in profile.columns if c.name == candidate.column), None)
    samples = ", ".join(col_profile.sample_values) if col_profile else ""

    system_prompt = (
        f"You are looking at ONE numeric column from a commercial/institutional banking "
        f"data table, deciding whether it is a genuinely meaningful measure to track over "
        f"time per client/agreement for anomaly detection (comparing a client's current "
        f"value against their own recent history).\n\n"
        f"Table: {profile.physical_table!r} ({candidate.n_entities} entities, "
        f"{candidate.observations_per_entity:.1f} observations/entity on average)\n"
        f"Column: {candidate.column!r} (numeric)\n"
        f"Sample values seen: {samples}\n"
        f"Other columns in this table: {', '.join(c.name for c in profile.columns if c.name != candidate.column)}\n\n"
        f"Rules:\n"
        f"1. is_relevant: false if this looks like an identifier, a code, a constant, or "
        f"a value that's already a ratio/normalised score (those don't need a challenger "
        f"baseline the same way a raw magnitude does) -- true only for a genuine business "
        f"magnitude an RM or credit analyst would want flagged when it moves unusually.\n"
        f"2. proposed_name: a short snake_case name for what this measures (not the "
        f"physical column name), e.g. 'covenant_headroom_pct', 'days_past_due'.\n"
        f"3. business_meaning: one sentence a non-technical relationship manager would understand.\n"
        f"4. confidence: your own 0..1 estimate.\n"
        f"5. caveats: anything that makes challenging this specific column risky or "
        f"low-value (e.g. sparse per-entity history, seasonal by nature, already capped).\n"
        f"This is a synthetic proof-of-concept dataset."
    )
    try:
        agent = Agent(model=model, tools=[], system_prompt=system_prompt)
        result = agent(f"Assess column {candidate.column!r} on table {profile.physical_table!r}.",
                       structured_output_model=_ProposedMeasure)
        parsed = result.structured_output
        if parsed is None:
            raise ValueError("agent returned no structured output")
    except Exception as e:  # noqa: BLE001 -- any failure -> rejected, never a guessed proposal
        return MeasureProposal(table=profile.physical_table, column=candidate.column, accepted=False,
                               rejected_reason=f"proposal failed: {type(e).__name__}: {e}")

    if not parsed.is_relevant:
        return MeasureProposal(table=profile.physical_table, column=candidate.column, accepted=False,
                               rejected_reason=f"model judged not relevant: {parsed.business_meaning or parsed.caveats}")

    if not parsed.proposed_name or not parsed.proposed_name.replace("_", "").isalnum():
        return MeasureProposal(table=profile.physical_table, column=candidate.column, accepted=False,
                               rejected_reason=f"proposed_name {parsed.proposed_name!r} is not a usable identifier")

    return MeasureProposal(
        table=profile.physical_table, column=candidate.column, accepted=True,
        proposed_name=parsed.proposed_name, business_meaning=parsed.business_meaning,
        confidence=parsed.confidence, caveats=parsed.caveats,
    )


def propose_ml_measures(eligibility: dict[str, list[MeasureEligibility]],
                        profiles: dict[str, TableProfile], model, *,
                        already_mapped: set[tuple[str, str]] = frozenset()) -> list[MeasureProposal]:
    """One small, isolated LLM call per Gate-1-eligible column not already
    covered by a canonical binding mapping (`already_mapped` = the
    (physical_table, physical_column) pairs a binding already maps to a
    real semantic-model concept -- Gate 2 already has those, they don't
    need a Gate-3 proposal). Never proposes a column that failed Gate 1
    -- the input list is already filtered by onboarding/ml_profiler.py's
    `eligible` flag, so nothing here can nominate a key or a constant."""
    proposals = []
    for table_name, columns in eligibility.items():
        profile = profiles[table_name]
        for candidate in columns:
            if not candidate.eligible:
                continue
            if (table_name, candidate.column) in already_mapped:
                continue
            proposals.append(_propose_one(candidate, profile, model))
    return proposals
