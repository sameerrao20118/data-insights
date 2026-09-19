"""
R4 -- concept proposer. onboarding/binding_proposer.py maps a schema's
tables onto the concepts config/semantic_model.yaml already has. A
table that maps onto NOTHING used to vanish into "unavailable"; now, if
it carries at least one Gate-1-eligible measure (onboarding/ml_profiler.py),
a local model is asked to PROPOSE a new canonical concept for it --
name, kind, key, fields -- written to concepts.proposed.yaml for human
review. Same validate-or-reject discipline as every proposer here: the
key and every field must be real columns, the kind must be one the
semantic model knows, the name must be a usable identifier; anything
else is a rejected proposal with the reason, never a silent drop.
Accepting a proposed concept is a HUMAN edit of config/semantic_model.yaml
-- deliberately not automated: a new concept changes what every binding
may claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from datainsights.semantic.binding import CONCEPT_KINDS
from onboarding.binding_proposer import ConceptProposal
from onboarding.ml_profiler import MeasureEligibility
from onboarding.profiler import TableProfile


class _ProposedConcept(BaseModel):
    name: str            # CamelCase, e.g. "CovenantTest"
    kind: str            # one of CONCEPT_KINDS
    key: str             # the physical column that identifies one row's subject
    fields: list[str]    # physical columns worth carrying, key included
    business_meaning: str
    confidence: float


@dataclass
class NewConceptProposal:
    table: str
    accepted: bool
    name: str | None = None
    kind: str | None = None
    key: str | None = None
    fields: list[str] = field(default_factory=list)
    business_meaning: str = ""
    confidence: float = 0.0
    rejected_reason: str | None = None


def uncovered_tables(profiles: dict[str, TableProfile], binding_proposals: dict[str, ConceptProposal],
                     eligibility: dict[str, list[MeasureEligibility]]) -> list[str]:
    """Profiled tables no accepted concept mapping uses AND that carry at
    least one Gate-1-eligible measure -- a lookup table with no history
    is not a concept candidate."""
    used = {p.entity for p in binding_proposals.values() if p.entity}
    return sorted(t for t in profiles if t not in used and any(c.eligible for c in eligibility.get(t, [])))


def _propose_one(table: str, profile: TableProfile, model) -> NewConceptProposal:
    from strands import Agent

    cols = ", ".join(f"{c.name}({c.inferred_type}{'*' if c.is_unique else ''})" for c in profile.columns)
    system_prompt = (
        f"A commercial banking data platform has canonical concepts (Party, Account, BalanceObservation, "
        f"Transaction, RiskGradeVersion, PartyMetricVersion, CollateralValuation). The physical table "
        f"{table!r} ({profile.row_count} rows) maps onto NONE of them. Propose ONE new canonical concept "
        f"for it.\nColumns: {cols}\nRules: name is CamelCase; kind is one of {list(CONCEPT_KINDS)}; key and "
        f"every field must be column names from the list above, character for character; business_meaning "
        f"is one sentence a relationship manager would understand; confidence 0..1. Synthetic POC data."
    )
    try:
        agent = Agent(model=model, tools=[], system_prompt=system_prompt)
        result = agent(f"Propose a concept for table {table!r}.", structured_output_model=_ProposedConcept)
        parsed = result.structured_output
        if parsed is None:
            raise ValueError("agent returned no structured output")
    except Exception as e:  # noqa: BLE001 -- any failure -> rejected, never a guessed concept
        return NewConceptProposal(table=table, accepted=False,
                                  rejected_reason=f"proposal failed: {type(e).__name__}: {e}")
    real = {c.name for c in profile.columns}
    problems = []
    if not parsed.name.isalnum() or not parsed.name[:1].isupper():
        problems.append(f"name {parsed.name!r} is not CamelCase")
    if parsed.kind not in CONCEPT_KINDS:
        problems.append(f"kind {parsed.kind!r} not in {CONCEPT_KINDS}")
    if parsed.key not in real:
        problems.append(f"key {parsed.key!r} is not a column of {table!r}")
    bad = [f for f in parsed.fields if f not in real]
    if bad:
        problems.append(f"fields {bad} are not columns of {table!r}")
    if problems:
        return NewConceptProposal(table=table, accepted=False, name=parsed.name, kind=parsed.kind,
                                  rejected_reason="; ".join(problems))
    return NewConceptProposal(table=table, accepted=True, name=parsed.name, kind=parsed.kind, key=parsed.key,
                              fields=list(dict.fromkeys([parsed.key] + parsed.fields)),
                              business_meaning=parsed.business_meaning, confidence=parsed.confidence)


def propose_concepts(profiles: dict[str, TableProfile], binding_proposals: dict[str, ConceptProposal],
                     eligibility: dict[str, list[MeasureEligibility]], model) -> list[NewConceptProposal]:
    return [_propose_one(t, profiles[t], model)
            for t in uncovered_tables(profiles, binding_proposals, eligibility)]
