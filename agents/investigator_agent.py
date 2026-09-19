"""
Investigator Agent (docs/agentic_plan.md A2) -- for a Recommendation
whose category came from a disclosed simplification
(config/domains_fdm.yaml's `ambiguous: true`, e.g. fixed_rate_expiry's
"TREASURY_OPPORTUNITY or HEDGING_NEED... direction-dependent"), gathers
more of that client's own evidence and PROPOSES a refinement -- it never
changes the Recommendation's actual category itself. An RM confirming
the proposal in the dashboard is what changes it (not built this pass;
see docs/agentic_plan.md's A3).

Tools are read-only and scoped to ONE client -- no tool here can reach
another party's data, and none can write anything. This is deliberately
narrower than agents/tools.py's detector tools: it doesn't run detection
logic, only reads what's already been computed or is directly on file
for this client, so an RM can trust "the investigator only looked at
this client's own record."

Same validate-or-fallback discipline as agents/domain_agent.py: the
proposed category MUST be one of the signal_type's registered
category_options (datainsights.domain_registry.category_options()) --
never free text, never a category outside that fixed set -- and every
cited evidence_ref must be real, not invented. Any validation failure
degrades to a needs_review InvestigationNote, never a guessed
refinement.

docs/generalization_plan.md Phase 4 -- the A2 consistency fix: a live
run once proposed HEDGING_NEED while its OWN reasoning said "no
multi-currency activity found in this client's own transaction
history" -- backwards, since absence of FX exposure is
TREASURY_OPPORTUNITY evidence, not HEDGING_NEED evidence. The model's
prose sounded plausible; the substance was self-contradicting. Fixed
with a rule, not a prompt tweak: config/domains_fdm.yaml's
category_options now name a `requires_evidence` predicate per option
(EVIDENCE_PREDICATES below); `_validate()` recomputes this client's
tool results directly in Python (never trusting the LLM's paraphrase of
what it saw) and rejects any proposal whose supporting predicate is
false, to needs_review.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from pydantic import BaseModel
from strands import tool

from agents.domain_agent import BANNED_TERMS
from datainsights.correlation.hypothesis import Recommendation
from datainsights.domain_registry import category_evidence_requirements as _category_evidence_requirements
from datainsights.domain_registry import category_for as _category_for
from datainsights.domain_registry import category_options as _category_options
from datainsights.prompts import load_prompt
from datainsights.semantic.canonical import CanonicalSource


class InvestigationFields(BaseModel):
    proposed_category: str
    reasoning: str  # one paragraph, must cite evidence_refs it used
    evidence_refs: list[str]  # every evidence_ref actually consulted
    could_not_determine: str  # honest statement of what evidence would resolve this, if not found


@dataclass
class InvestigationNote:
    prty_id: str
    original_category: str
    proposed_category: str
    reasoning: str
    evidence_refs: tuple[str, ...]
    could_not_determine: str
    narrative_source: str
    latency_seconds: float = 0.0
    status: str = "proposed"  # "proposed" | "no_change_proposed" | "needs_review"


def _get_client_context(canonical: CanonicalSource, prty_id: str, as_of: date) -> dict:
    party = canonical.read("Party", as_at=as_of, party_id=prty_id)
    if party.empty:
        return {"status": "not_found"}
    row = party.iloc[0]
    return {
        "segment": row.get("segment", ""),
        "sector": row.get("sector_code", ""),
        "country": row.get("country_code", ""),
    }


def _get_currency_exposure(canonical: CanonicalSource, prty_id: str, as_of: date) -> dict:
    accounts = canonical.read("Account", as_at=as_of, party_id=prty_id)
    currencies: set[str] = set()
    for account_id in accounts.get("account_id", []):
        tx = canonical.read("Transaction", account_id=account_id)
        if not tx.empty and "currency" in tx.columns:
            currencies |= set(tx["currency"].dropna().unique())
    non_eur = currencies - {"EUR"}
    return {"currencies_seen": sorted(currencies), "non_eur_currencies": sorted(non_eur)}


def _get_recent_balance_trend(canonical: CanonicalSource, prty_id: str, as_of: date) -> dict:
    accounts = canonical.read("Account", as_at=as_of, party_id=prty_id)
    dep = accounts[accounts["product_class"] == "deposit"]
    if dep.empty:
        return {"status": "no_deposit_account"}
    bal = canonical.read("BalanceObservation", account_id=dep.iloc[0]["account_id"])
    bal = bal[(pd.to_datetime(bal["observed_at"]) >= pd.Timestamp(as_of - timedelta(days=90)))
             & (pd.to_datetime(bal["observed_at"]) <= pd.Timestamp(as_of))].sort_values("observed_at")
    if bal.empty:
        return {"status": "no_balance_history"}
    first, last = float(bal.iloc[0]["balance"]), float(bal.iloc[-1]["balance"])
    return {"status": "ok", "balance_90d_ago": round(first, 2), "balance_now": round(last, 2),
            "direction": "rising" if last > first else "flat_or_falling"}


def make_investigator_tools(canonical: CanonicalSource, prty_id: str, as_of: date) -> list:
    """Read-only tools, closed over ONE prty_id -- structurally cannot
    reach another client's data, matching agents/tools.py's binding
    pattern but with no write/detect capability at all. Reads through
    CanonicalSource (docs/generalization_plan.md Phase 1), so these work
    unchanged against any bound schema -- a binding missing sector/
    country (e.g. legacy) just answers with empty strings, never a
    crash. Thin wrappers over the module-level `_get_*` functions above
    so `investigate()` can recompute the SAME facts directly in Python
    for `_validate()`'s evidence check, rather than trusting whatever
    the LLM claims it saw."""

    @tool
    def get_client_context() -> dict:
        """This client's segment, sector, and country -- for context
        only, never a basis to invent a number."""
        return _get_client_context(canonical, prty_id, as_of)

    @tool
    def get_currency_exposure() -> dict:
        """Whether this client's own recorded transactions show any
        non-EUR activity -- the deciding evidence for TREASURY_OPPORTUNITY
        (simple excess cash) vs HEDGING_NEED (multi-currency exposure)."""
        return _get_currency_exposure(canonical, prty_id, as_of)

    @tool
    def get_recent_balance_trend() -> dict:
        """This client's deposit balance direction over the last 90
        days -- rising balances lean TREASURY_OPPORTUNITY (idle cash to
        place), flat/no-deposit-account leans neither way on its own."""
        return _get_recent_balance_trend(canonical, prty_id, as_of)

    return [get_client_context, get_currency_exposure, get_recent_balance_trend]


# --- evidence predicates (docs/generalization_plan.md Phase 4, A2 fix) -----
#
# Each predicate answers one yes/no question against the SAME facts
# make_investigator_tools() exposes to the model -- computed directly
# here, not parsed out of the model's prose, so a predicate can never be
# fooled by reasoning that merely SOUNDS grounded.

def _predicate_non_eur_currency_activity(tool_results: dict) -> bool:
    return bool(tool_results.get("get_currency_exposure", {}).get("non_eur_currencies"))


def _predicate_balance_rising(tool_results: dict) -> bool:
    trend = tool_results.get("get_recent_balance_trend", {})
    return trend.get("status") == "ok" and trend.get("direction") == "rising"


EVIDENCE_PREDICATES = {
    "non_eur_currency_activity": _predicate_non_eur_currency_activity,
    "balance_rising": _predicate_balance_rising,
}


def _validate(parsed: InvestigationFields, options: list[str], evidence_requirements: dict[str, str],
             tool_results: dict) -> list[str]:
    """Every check here runs regardless of what the model claims -- the
    gate an investigation cannot talk its way past. Mirrors
    agents/domain_agent.py's own _validate but for a category proposal
    instead of narrative prose."""
    problems = []
    if parsed.proposed_category not in options:
        problems.append(f"proposed_category {parsed.proposed_category!r} not in {options}")
    else:
        predicate_name = evidence_requirements.get(parsed.proposed_category)
        if predicate_name:
            predicate = EVIDENCE_PREDICATES[predicate_name]
            if not predicate(tool_results):
                problems.append(
                    f"proposed_category {parsed.proposed_category!r} requires evidence "
                    f"{predicate_name!r}, but this client's own tool results don't show it "
                    f"(tool_results={tool_results})"
                )

    text_blob = f"{parsed.reasoning} {parsed.could_not_determine}".lower()
    for banned in BANNED_TERMS:
        if banned in text_blob:
            problems.append(f"investigation used a disallowed term: {banned!r}")
    if not parsed.reasoning.strip():
        problems.append("empty reasoning")

    return problems


def investigation_options(recommendation: Recommendation, signals=None) -> list[str]:
    """R9: what an investigator may choose between. An `ambiguous` signal
    type has registered category_options; a MULTI-DOMAIN recommendation
    (R9's second trigger) may choose between the categories its own
    confirming signals map to, plus the one assemble() picked. Never
    free text, never a category outside this list."""
    options = list(_category_options(recommendation.endogenous_signal_type))
    if not options and signals and len({s.domain for s in signals}) > 1:
        options = sorted({_category_for(s.signal_type) for s in signals} | {recommendation.nba_category})
        if len(options) < 2:
            options = []  # every domain agrees -- nothing to investigate
    return options


def investigate(recommendation: Recommendation, canonical: CanonicalSource, model, as_of: date,
                signals=None) -> InvestigationNote | None:
    """Meaningful when recommendation.ambiguous OR the recommendation is
    confirmed by more than one domain (R9's two Tier-2 triggers) --
    callers should check that before spending an LLM call. Returns None
    (not a note) if there are no category options to choose between,
    since there's nothing to investigate."""
    options = investigation_options(recommendation, signals)
    if not options:
        return None
    evidence_requirements = _category_evidence_requirements(recommendation.endogenous_signal_type)

    t0 = time.monotonic()
    tools = make_investigator_tools(canonical, recommendation.prty_id, as_of)
    template, prompt_version = load_prompt("investigator_agent")
    system_prompt = template.format(
        nba_category=recommendation.nba_category, options=options,
        evidence_ref=recommendation.evidence_ref,
    )
    try:
        from strands import Agent

        agent = Agent(model=model, tools=tools, system_prompt=system_prompt)
        result = agent(
            f"Investigate client {recommendation.prty_id}'s recommendation. Original "
            f"hypothesis: {recommendation.hypothesis!r}. Existing evidence_ref: "
            f"{recommendation.evidence_ref!r}.",
            structured_output_model=InvestigationFields,
        )
        parsed = result.structured_output
        if parsed is None:
            return None
    except Exception as e:  # noqa: BLE001 -- any failure -> no note, never raises
        return InvestigationNote(
            prty_id=recommendation.prty_id, original_category=recommendation.nba_category,
            proposed_category=recommendation.nba_category, reasoning="",
            evidence_refs=(), could_not_determine=f"investigation failed: {type(e).__name__}: {e}",
            narrative_source=f"investigator_fallback ({type(e).__name__})",
            latency_seconds=time.monotonic() - t0, status="needs_review",
        )

    # Recomputed directly against this client's own data, never trusted
    # from the model's tool-call transcript -- see this module's
    # docstring on why (a live run's reasoning claimed the opposite of
    # what its own cited evidence showed).
    tool_results = {
        "get_client_context": _get_client_context(canonical, recommendation.prty_id, as_of),
        "get_currency_exposure": _get_currency_exposure(canonical, recommendation.prty_id, as_of),
        "get_recent_balance_trend": _get_recent_balance_trend(canonical, recommendation.prty_id, as_of),
    }
    problems = _validate(parsed, options, evidence_requirements, tool_results)

    if problems:
        return InvestigationNote(
            prty_id=recommendation.prty_id, original_category=recommendation.nba_category,
            proposed_category=recommendation.nba_category, reasoning="",
            evidence_refs=(), could_not_determine="; ".join(problems),
            narrative_source="investigator_fallback (validation failed)",
            latency_seconds=time.monotonic() - t0, status="needs_review",
        )

    status = "proposed" if parsed.proposed_category != recommendation.nba_category else "no_change_proposed"
    return InvestigationNote(
        prty_id=recommendation.prty_id, original_category=recommendation.nba_category,
        proposed_category=parsed.proposed_category, reasoning=parsed.reasoning,
        evidence_refs=tuple(parsed.evidence_refs), could_not_determine=parsed.could_not_determine,
        narrative_source="strands+ollama:investigator", latency_seconds=time.monotonic() - t0,
        status=status,
    )
