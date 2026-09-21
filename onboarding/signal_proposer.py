"""
Stage C of signal discovery (docs/signal_discovery_design.md rec. 6):
an LLM proposal for a candidate that survived deterministic screening.

Structurally identical to onboarding/ml_measure_proposer.py, on purpose:
same one-small-call-per-candidate isolation (a bad proposal never affects
another), same validate-or-reject discipline, same rule that anything
unverifiable is DROPPED and logged in `rejected`, never silently kept.

What the model is asked for: a readable name, the NBA category, a why_now
line and a hypothesis sentence. What it is NOT asked for: whether the
pattern is real (Stage B already measured that), or a new category
(the choice is constrained to config/categories.yaml's registered set --
inventing a seventh category is a governance decision, not a model one).

Every proposal lands as `status: shadow`. Nothing here reaches an RM.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pydantic import BaseModel, Field

from datainsights import category_registry
from detection_engine.specs.archetypes import SignalSpec
from onboarding.signal_enumerator import Candidate
from onboarding.signal_screener import ScreenResult


class _ProposedSignal(BaseModel):
    """Deliberately small. Measured on qwen2.5:7b: a 7-field schema made
    the model reason correctly in prose but fail to invoke the structured
    output tool at all (StructuredOutputException on every candidate).
    Five fields, each with an explicit description, is what this model
    class reliably fills. `why_now` is derived from the hypothesis rather
    than asked for separately, for the same reason."""

    is_meaningful: bool = Field(description="true only if a relationship manager could act on this")
    proposed_name: str = Field(description="short snake_case business name, e.g. deposit_concentration_shift")
    category: str = Field(description="exactly one of the allowed NBA categories listed in the prompt")
    hypothesis: str = Field(description="one or two sentences on what this pattern usually means commercially")
    confidence: float = Field(description="your own estimate between 0 and 1")


@dataclass
class SignalProposal:
    candidate_signal_type: str
    archetype: str
    accepted: bool
    spec: SignalSpec | None = None
    proposed_name: str | None = None
    category: str = ""
    why_now: str = ""
    hypothesis: str = ""
    confidence: float = 0.0
    caveats: str = ""
    screening: dict | None = None
    rejected_reason: str | None = None   # non-empty ONLY when accepted is False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.pop("spec", None)
        return payload


def _describe(candidate: Candidate, screen: ScreenResult) -> str:
    spec = candidate.spec
    params = ", ".join(f"{k}={v!r}" for k, v in spec.params.items()
                       if k not in {"min_observations", "median_mode"})
    return (
        f"Archetype: {spec.archetype}\n"
        f"Canonical concept: {spec.concept} (grain: {', '.join(spec.grain)})\n"
        f"Parameters: {params}\n"
        f"Plain-language rule: {candidate.rationale}\n"
        f"Empirical screening on real data:\n"
        f"  - fires on {screen.fire_rate:.1%} of clients ({screen.n_flagged} clients)\n"
        f"  - fire rate varies {screen.rate_swing:.1%} across as-of dates {list(screen.as_of_dates)}\n"
        f"  - highest client overlap with an EXISTING signal: {screen.max_overlap:.0%}"
        f"{f' (vs {screen.overlaps_with!r})' if screen.overlaps_with else ''}\n"
    )


def _propose_one(candidate: Candidate, screen: ScreenResult, model) -> SignalProposal:
    from strands import Agent

    valid_categories = sorted(category_registry.category_names())
    spec = candidate.spec

    system_prompt = (
        "You are a commercial/institutional banking data analyst reviewing ONE candidate "
        "detection rule that a deterministic screening process found in a bank's own client "
        "data. The rule already passed statistical screening: it fires on a meaningful "
        "minority of clients, it is stable over time, and it flags a DIFFERENT set of "
        "clients than the bank's existing detectors.\n\n"
        "Your job is to judge whether it is BUSINESS-meaningful, and if so, to name and "
        "explain it for a relationship manager.\n\n"
        f"{_describe(candidate, screen)}\n"
        "Rules:\n"
        "1. is_meaningful: false if this rule is a tautology, a restatement of an "
        "identifier, or something no relationship manager could act on. Be willing to say "
        "false -- a statistically novel pattern can still be business noise.\n"
        f"2. category: MUST be copied exactly from this list: {valid_categories}. Choose "
        "ADVISORY_ONLY if the pattern is worth a conversation but implies no specific "
        "product. Choose a revenue category ONLY if it plausibly indicates a product need.\n"
        "3. proposed_name: short snake_case, describing the BUSINESS meaning, not the "
        "mechanics. e.g. 'deposit_concentration_shift', not 'balance_deviation_w90_k21'.\n"
        "4. hypothesis: 1-2 sentences on what the pattern usually means commercially. "
        "Do not claim certainty; describe the usual interpretation.\n"
        "5. confidence: your own 0..1 estimate that this is genuinely useful.\n\n"
        "This is a synthetic proof-of-concept dataset. Never claim a real-world outcome.\n\n"
        "Answer ONLY by calling the structured output tool with those five fields. "
        "Do not write prose. Example of a well-formed answer:\n"
        '{"is_meaningful": true, "proposed_name": "deposit_concentration_shift", '
        '"category": "TREASURY_OPPORTUNITY", "hypothesis": "A sustained shift in where '
        'balances sit usually means the client is reorganising cash, which is a natural '
        'moment to review placement.", "confidence": 0.62}'
    )

    try:
        agent = Agent(model=model, tools=[], system_prompt=system_prompt)
        result = agent(
            f"Assess the candidate rule {spec.signal_type!r} on concept {spec.concept!r}.",
            structured_output_model=_ProposedSignal,
        )
        parsed = result.structured_output
        if parsed is None:
            raise ValueError("agent returned no structured output")
    except Exception as e:  # noqa: BLE001 -- any failure -> rejected, never a guessed proposal
        return SignalProposal(spec.signal_type, spec.archetype, accepted=False,
                              screening=candidate.screening,
                              rejected_reason=f"proposal failed: {type(e).__name__}: {e}")

    if not parsed.is_meaningful:
        return SignalProposal(spec.signal_type, spec.archetype, accepted=False,
                              screening=candidate.screening,
                              rejected_reason=f"model judged not business-meaningful: "
                                              f"{parsed.hypothesis}")

    name = (parsed.proposed_name or "").strip()
    if not name or not name.replace("_", "").isalnum():
        return SignalProposal(spec.signal_type, spec.archetype, accepted=False,
                              screening=candidate.screening,
                              rejected_reason=f"proposed_name {name!r} is not a usable identifier")

    # The one hard validation: an unregistered category is rejected, never
    # coerced. A model inventing "GROWTH_OPPORTUNITY" must not silently
    # become ADVISORY_ONLY -- that would hide the failure.
    if parsed.category not in valid_categories:
        return SignalProposal(spec.signal_type, spec.archetype, accepted=False,
                              screening=candidate.screening,
                              rejected_reason=f"category {parsed.category!r} is not registered "
                                              f"in config/categories.yaml (valid: {valid_categories})")

    renamed = SignalSpec(
        signal_type=name, archetype=spec.archetype, domain=spec.domain, concept=spec.concept,
        grain=spec.grain, params=spec.params, direction=spec.direction,
        origin="discovered", status="shadow",
        notes=f"discovered from {spec.signal_type}; {candidate.rationale}",
    )

    # why_now is derived, not asked for: a smaller schema is what this
    # model class fills reliably (see _ProposedSignal's docstring). The
    # screening numbers are factual, so this line states evidence rather
    # than inventing urgency the model did not claim.
    why_now = (f"Pattern observed on this client; flags {screen.fire_rate:.0%} of the book "
               f"and overlaps existing signals by {screen.max_overlap:.0%}.")

    return SignalProposal(
        candidate_signal_type=spec.signal_type, archetype=spec.archetype, accepted=True,
        spec=renamed, proposed_name=name, category=parsed.category,
        why_now=why_now, hypothesis=parsed.hypothesis,
        confidence=float(parsed.confidence),
        caveats="proposer schema reduced to 5 fields for local-model reliability; "
                "no model-authored caveats captured",
        screening=candidate.screening,
    )


def propose_signals(candidates: list[Candidate], screens: dict[str, ScreenResult],
                    model) -> list[SignalProposal]:
    """One isolated LLM call per candidate that PASSED screening. A
    candidate that failed Stage B never reaches the model -- the LLM is
    asked to interpret evidence, never to find it."""
    proposals = []
    for candidate in candidates:
        screen = screens.get(candidate.spec.signal_type)
        if screen is None or not screen.passed:
            continue
        proposals.append(_propose_one(candidate, screen, model))
    return proposals
