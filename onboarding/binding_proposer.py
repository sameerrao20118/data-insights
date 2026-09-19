"""
Phase 5a schema onboarding -- the binding proposer (docs/generalization_plan.md).
For each canonical concept in config/semantic_model.yaml, asks a local
Ollama model which profiled physical table and columns it maps to, with
a confidence and the evidence it used.

Same validate-or-fallback discipline as every other agent in this repo
-- the LLM PROPOSES, this module never trusts it blindly: every
proposed entity must be one of the profiled tables, every proposed
column must actually exist on that table's profile, or the mapping is
dropped, not silently kept. This is the ONE agent in this repo whose
proposal, once a human accepts it via `onboarding/accept.py`, becomes
literal pipeline configuration rather than narration text -- which is
exactly why the human-acceptance gate is mandatory, never bypassable,
and why every dropped/rejected mapping is surfaced in the review report,
not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel

from datainsights.semantic.binding import load_semantic_model
from onboarding.profiler import TableProfile

# R4: the concept list is config/semantic_model.yaml, loaded -- never a
# Python copy that can drift from the registry.
def semantic_concepts() -> dict[str, list[str]]:
    model = load_semantic_model()
    return {name: spec.field_names for name, spec in model.concepts.items()}


class _ProposedMapping(BaseModel):
    entity: str  # a profiled physical_table name, or the literal string "none"
    field_mappings: dict[str, str]  # canonical_field -> physical_column (only fields it found a match for)
    confidence: float  # 0..1, the model's own estimate
    evidence: str  # one sentence: which column names/samples this was based on
    unavailable_reason: str  # non-empty ONLY when entity == "none"


@dataclass
class ConceptProposal:
    concept: str
    entity: str | None
    field_mappings: dict[str, str]
    confidence: float
    evidence: str
    unavailable_reason: str | None
    rejected: list[str] = field(default_factory=list)  # proposed-but-invalid mappings, dropped and logged


def _profile_summary(profiles: dict[str, TableProfile]) -> str:
    lines = []
    for name, p in profiles.items():
        cols = ", ".join(f"{c.name}({c.inferred_type}{'*' if c.is_unique else ''})" for c in p.columns)
        lines.append(f"- {name} [{p.row_count} rows]: {cols}")
    return "\n".join(lines)


def _propose_one_concept(concept: str, fields: list[str], profiles: dict[str, TableProfile], model) -> ConceptProposal:
    from strands import Agent

    system_prompt = (
        f"You are proposing how a physical database schema maps onto ONE canonical "
        f"concept called {concept!r} in a commercial banking data platform. The "
        f"concept needs these fields (a physical column may not exist for all of "
        f"them -- that's fine, map what you can): {fields}.\n\n"
        f"Profiled physical tables and their columns (type shown in parens, "
        f"'*' means the column looks like a unique key):\n{_profile_summary(profiles)}\n\n"
        f"Rules:\n"
        f"1. entity MUST be exactly one of the profiled table names above, "
        f"character-for-character, or the literal string 'none' if nothing profiled "
        f"plausibly represents this concept.\n"
        f"2. field_mappings: canonical field name -> a column that ACTUALLY EXISTS on "
        f"your chosen entity, from the list above. Never invent a column name.\n"
        f"3. confidence is your own 0..1 estimate of how sure you are.\n"
        f"4. evidence: one sentence citing the actual column names or sample values "
        f"that led you to this mapping.\n"
        f"5. If entity is 'none', unavailable_reason must explain what's missing; "
        f"otherwise leave it empty.\n"
        f"This is a synthetic proof-of-concept dataset."
    )
    try:
        agent = Agent(model=model, tools=[], system_prompt=system_prompt)
        result = agent(f"Propose the mapping for concept {concept!r}.", structured_output_model=_ProposedMapping)
        parsed = result.structured_output
        if parsed is None:
            raise ValueError("agent returned no structured output")
    except Exception as e:  # noqa: BLE001 -- any failure -> unavailable, never a guessed mapping
        return ConceptProposal(concept=concept, entity=None, field_mappings={}, confidence=0.0,
                               evidence="", unavailable_reason=f"proposal failed: {type(e).__name__}: {e}")

    if parsed.entity == "none" or parsed.entity not in profiles:
        reason = parsed.unavailable_reason or f"model proposed entity {parsed.entity!r}, not a profiled table"
        return ConceptProposal(concept=concept, entity=None, field_mappings={}, confidence=parsed.confidence,
                               evidence=parsed.evidence, unavailable_reason=reason)

    real_columns = {c.name for c in profiles[parsed.entity].columns}
    accepted, rejected = {}, []
    for canon_field, phys_col in parsed.field_mappings.items():
        if canon_field in fields and phys_col in real_columns:
            accepted[canon_field] = phys_col
        else:
            rejected.append(f"{canon_field} -> {phys_col!r} (not a real column of {parsed.entity!r}, dropped)")

    return ConceptProposal(concept=concept, entity=parsed.entity, field_mappings=accepted,
                           confidence=parsed.confidence, evidence=parsed.evidence,
                           unavailable_reason=None, rejected=rejected)


def propose_binding(profiles: dict[str, TableProfile], model) -> dict[str, ConceptProposal]:
    """One proposal per canonical concept -- a separate, small LLM call
    each, so a bad/failed proposal for one concept (e.g. CollateralValuation,
    genuinely absent from most schemas) never affects another."""
    return {concept: _propose_one_concept(concept, fields, profiles, model)
           for concept, fields in semantic_concepts().items()}
