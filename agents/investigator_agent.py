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
degrades to `None` (no note), never a guessed refinement.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import BaseModel
from strands import tool

from agents.domain_agent import BANNED_TERMS
from datainsights.correlation.hypothesis import Recommendation
from datainsights.domain_registry import category_options as _category_options
from datainsights.sources.fdm_local import FdmLocalSource


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


def make_investigator_tools(source: FdmLocalSource, prty_id: str, as_of: date) -> list:
    """Read-only tools, closed over ONE prty_id -- structurally cannot
    reach another client's data, matching agents/tools.py's binding
    pattern but with no write/detect capability at all."""

    @tool
    def get_client_context() -> dict:
        """This client's segment, sector, and country -- for context
        only, never a basis to invent a number."""
        party = source.party(as_of)
        row = party[party["PRTY_ID"] == prty_id]
        if row.empty:
            return {"status": "not_found"}
        demo = source.party_demographic()
        demo_row = demo[demo["PRTY_ID"] == prty_id]
        loc = source.party_locator()
        loc_row = loc[loc["PRTY_ID"] == prty_id]
        return {
            "segment": row.iloc[0].get("PRTY_SGMNT_CD", ""),
            "sector": demo_row.iloc[0].get("NACE_SECTION_CD", "") if not demo_row.empty else "",
            "country": loc_row.iloc[0].get("COUNTRY_CD", "") if not loc_row.empty else "",
        }

    @tool
    def get_currency_exposure() -> dict:
        """Whether this client's own recorded transactions show any
        non-EUR activity -- the deciding evidence for TREASURY_OPPORTUNITY
        (simple excess cash) vs HEDGING_NEED (multi-currency exposure).
        Every FDM amount this build's generator writes is EUR
        (detection_engine's own CURRENCY_MARKERS note) -- so an honest
        answer here today is almost always 'no non-EUR activity found',
        which is itself the correct, disclosed finding, not a tool
        failure."""
        events = source.financial_event(as_of - timedelta(days=365), as_of)
        currencies = set(events.get("FIN_EVNT_CURY_CD", []).unique()) if "FIN_EVNT_CURY_CD" in events else set()
        non_eur = currencies - {"EUR"}
        return {"currencies_seen": sorted(currencies), "non_eur_currencies": sorted(non_eur)}

    @tool
    def get_recent_balance_trend() -> dict:
        """This client's deposit balance direction over the last 90
        days -- rising balances lean TREASURY_OPPORTUNITY (idle cash to
        place), flat/no-deposit-account leans neither way on its own."""
        agr = source.agreement(as_of)
        pa, _ = source.read_entity("PARTY_AGREEMENT", allow_unbounded=True)
        ids = pa.loc[pa["PRTY_ID"] == prty_id, "AGRMNT_ID"]
        dep = agr[agr["AGRMNT_ID"].isin(ids) & (agr["AGRMNT_TYP_CD"] == "DEP")]
        if dep.empty:
            return {"status": "no_deposit_account"}
        bal = source.daily_balance(as_of - timedelta(days=90), as_of)
        bal = bal[bal["AGRMNT_ID"] == dep.iloc[0]["AGRMNT_ID"]].sort_values("AGRMNT_DLY_BAL_STRT_DTTM")
        if bal.empty:
            return {"status": "no_balance_history"}
        first, last = float(bal.iloc[0]["AGRMNT_LDGR_BAL_AMT"]), float(bal.iloc[-1]["AGRMNT_LDGR_BAL_AMT"])
        return {"status": "ok", "balance_90d_ago": round(first, 2), "balance_now": round(last, 2),
                "direction": "rising" if last > first else "flat_or_falling"}

    return [get_client_context, get_currency_exposure, get_recent_balance_trend]


def investigate(recommendation: Recommendation, source: FdmLocalSource, model, as_of: date
                ) -> InvestigationNote | None:
    """Only meaningful when recommendation.ambiguous -- callers should
    check that before spending an LLM call. Returns None (not a note)
    if the signal type has no registered category_options to choose
    between, since there's nothing to investigate."""
    options = _category_options(recommendation.endogenous_signal_type)
    if not options:
        return None

    t0 = time.monotonic()
    tools = make_investigator_tools(source, recommendation.prty_id, as_of)
    system_prompt = (
        f"You are investigating ONE ambiguous recommendation for a commercial "
        f"banking client. The category was provisionally set to "
        f"{recommendation.nba_category!r} as a simplification. Your job is to "
        f"gather this client's OWN evidence using the tools available and "
        f"propose which of these categories actually fits best: {options}. Rules:\n"
        f"1. Call every available tool before answering.\n"
        f"2. proposed_category MUST be exactly one string from this list: {options}.\n"
        f"3. evidence_refs MUST list only evidence_ref strings you can construct "
        f"from actual tool results, or the client's existing evidence_ref "
        f"{recommendation.evidence_ref!r} -- never invent one.\n"
        f"4. reasoning must be grounded ONLY in what the tools returned -- "
        f"never invent a fact.\n"
        f"5. could_not_determine: state plainly what evidence would have "
        f"resolved this if the tools didn't show it (e.g. 'no multi-currency "
        f"activity found in this client's own transaction history').\n"
        f"6. Never mention financial crime, sanctions, PEP status, or "
        f"politically exposed persons.\n"
        f"This is a synthetic proof-of-concept dataset."
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

    problems = []
    if parsed.proposed_category not in options:
        problems.append(f"proposed_category {parsed.proposed_category!r} not in {options}")
    text_blob = f"{parsed.reasoning} {parsed.could_not_determine}".lower()
    for banned in BANNED_TERMS:
        if banned in text_blob:
            problems.append(f"investigation used a disallowed term: {banned!r}")
    if not parsed.reasoning.strip():
        problems.append("empty reasoning")

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
        narrative_source=f"strands+ollama:investigator", latency_seconds=time.monotonic() - t0,
        status=status,
    )
