"""
Hypothesis Assembler -- docs/decision_record.md Tab 3's four composition
rules, implemented verbatim:

  1. RISK_REVIEW suppresses revenue-category signals
  2. multi-domain confirmation curve
  3. exogenous alignment
  4. decomposable strength score

Produces a Recommendation carrying exactly the insight_attributes keys
from docs/decision_record.md Tab 4 (the MIMO insight record shape).

Reuses this repo's EXISTING category/hypothesis/sizing conventions from
datainsights/worklist.py rather than reinventing them -- categorize_macro_event,
macro_hypothesis, and the TENDER_ADVANCE_PCT sizing constant are imported,
not duplicated. This module's own job is the NEW part: combining multiple
domain signals for one client into one ranked, sized recommendation,
which nothing in worklist.py does today (it maps one detection at a
time).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

from datainsights.domain_registry import category_for as _category_for
from datainsights.domain_registry import hypothesis_for as _hypothesis_for
from datainsights.domain_registry import is_ambiguous as _is_ambiguous
from datainsights.worklist import TENDER_ADVANCE_PCT
from detection_engine.signal import Signal

# Endogenous signal_type -> NBA category, and -> the general reasoning
# sentence, now live in config/domains_fdm.yaml (one block per domain),
# not here -- see datainsights/domain_registry.py's docstring for why
# this module reads them directly rather than via a passed-in rules dict.
# docs/adding_a_new_domain.md is the checklist for adding a signal_type's
# mapping there.

REVENUE_CATEGORIES = {"FINANCING_NEED", "TREASURY_OPPORTUNITY", "HEDGING_NEED", "CAPEX_FINANCING"}


@dataclass(frozen=True)
class Recommendation:
    prty_id: str
    nba_category: str
    crm_text: str
    hypothesis: str
    recommended_action: str
    business_value_score: int
    confirming_domains: tuple[str, ...]
    signal_strength: int  # 1-5, decision record Tab 4's scale
    endogenous_signal_type: str
    exogenous_event_type: str | None
    exogenous_event_source: str | None
    exogenous_event_date: str | None  # dd/mm/yyyy per Tab 4's platform type convention
    evidence_ref: str
    sizing_basis: str
    response_actions: tuple[str, ...] = ("Customer Engaged", "Not Appropriate", "Remind Me Later")
    # The machine-usable amounts behind recommended_action's prose. The
    # prose is what an RM reads; these are what downstream consumers
    # (datainsights/fdm_worklist.py's revenue model, any future ranker)
    # read, so nothing ever has to parse a number back out of a sentence.
    # None where the signal could not be honestly sized -- see sizing_basis.
    raw_event_value_eur: float | None = None
    sized_offer_eur: float | None = None
    # Stable across reruns on the same evidence (never regenerated per
    # run) so a Pega->CRM response (Customer Engaged / Not Appropriate /
    # Remind Me Later, docs/decision_record.md Tab 1/D6) can join back to
    # this exact recommendation as a future ML training label -- schema
    # only, no propensity model built here. Every sink carries this
    # verbatim; see tests/test_sink_contract.py.
    recommendation_id: str = ""
    # Which SLOT E2 baseline produced the strongest signal ("deterministic"
    # or "isolation_forest") -- traceability for the RM digest/worklist.
    # Rule-based detectors with no baseline concept report "deterministic".
    baseline_source: str = "deterministic"
    # A2 (docs/agentic_plan.md): true when the strongest signal's category
    # mapping is a disclosed simplification (config/domains_fdm.yaml's
    # `ambiguous:` flag) worth agents/investigator_agent.py's second look.
    # This category is still what assemble() decided -- an investigator
    # may PROPOSE a refinement (a separate, unconfirmed field elsewhere),
    # never change this one itself.
    ambiguous: bool = False


@dataclass(frozen=True)
class EndogenousSizing:
    """Offer sizing for endogenous-only signals. Defaults mirror
    config/rules.yaml's `fdm_endogenous_sizing`; callers with the rules
    dict should use from_rules_dict so review-time edits take effect."""
    target_utilization_pct: float = 0.70
    min_offer_eur: float = 10_000

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "EndogenousSizing":
        d = rules.get("fdm_endogenous_sizing", {})
        return cls(
            target_utilization_pct=d.get("target_utilization_pct", cls.target_utilization_pct),
            min_offer_eur=d.get("min_offer_eur", cls.min_offer_eur),
        )


UNSIZED_BASIS = "not_sized_this_pass_no_revenue_figure_on_fdm_party"

# Readable action when a revenue-category signal can't be honestly sized.
# Never a raw dict -- this text is what an RM reads.
UNSIZED_ACTION = {
    "cash_buildup": "RM to discuss where the client's growing balance is placed -- "
                    "build-up too small to size a placement offer.",
    "revenue_pattern_change": "RM to review the structural change in the client's incoming "
                               "payments -- no offer is sized without a confirmed contract or "
                               "revenue figure.",
    "facility_utilization_spike": "RM to review facility headroom with the client -- no limit "
                                   "increase sized from the available figures.",
    "facility_maturity_approaching": "RM to start the renewal conversation before the facility "
                                      "matures -- current limit unavailable to size the renewal.",
    "fixed_rate_expiry": "RM to discuss refix or refinancing options before the fixed rate ends "
                          "-- no offer sized without the outstanding balance.",
}

# Action for categories that never carry an offer.
NON_REVENUE_ACTION = {
    "collateral_coverage_drop": "Refer to Credit Risk for a collateral adequacy review -- "
                                 "internal action, not a client sales conversation.",
    "dormancy": "RM to make a relationship check-in on the inactive account -- no product offer.",
}
# A revenue signal whose category was suppressed (rule 1). Deliberately
# generic: the reason must never be surfaced in text -- see assemble()'s
# docstring on HIGH_RSK_CUST_IND.
SUPPRESSED_ACTION = "RM to review before any product conversation -- no offer sized for this recommendation."


def _eur(value: float) -> str:
    return f"EUR {float(value):,.0f}"


def _size_endogenous(signal: Signal, sizing: EndogenousSizing) -> tuple[float | None, str, str]:
    """(sized_offer_eur, sizing_basis, recommended_action) sized from the
    client's own figures carried on the Signal, or an honest unsized
    action. Any missing/malformed figure yields unsized, never a guess."""
    m = signal.raw_measure or {}
    kind = signal.signal_type
    try:
        if kind == "cash_buildup":
            surplus = float(m["current_balance"]) - float(m["prior_balance"])
            if surplus >= sizing.min_offer_eur:
                return (round(surplus, 2), "balance_buildup_amount_illustrative",
                        f"Offer a deposit or short-term investment placement for ~{_eur(surplus)} "
                        f"(the balance build-up over the review window, illustrative).")
        elif kind == "facility_utilization_spike":
            drawn, limit = float(m["drawn_amount"]), float(m["orig_limit"])
            increase = drawn / sizing.target_utilization_pct - limit
            if limit > 0 and increase >= sizing.min_offer_eur:
                return (round(increase, 2),
                        f"limit_increase_to_{round(sizing.target_utilization_pct * 100)}pct_utilization_illustrative",
                        f"Offer a facility limit increase of ~{_eur(increase)} to bring utilisation back "
                        f"to {sizing.target_utilization_pct:.0%} (currently {drawn / limit:.0%} of "
                        f"{_eur(limit)}, illustrative).")
        elif kind == "facility_maturity_approaching":
            limit = float(m["orig_limit"])
            if limit >= sizing.min_offer_eur:
                return (round(limit, 2), "renewal_at_current_limit_illustrative",
                        f"Offer renewal of the {_eur(limit)} facility before it matures on "
                        f"{m.get('close_date')} (in {m.get('days_to_close')} days), using the current "
                        f"limit as the starting point (illustrative).")
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return None, UNSIZED_BASIS, UNSIZED_ACTION.get(kind, "RM to review this signal with the client -- no offer sized.")


def _decomposable_strength(signals: list[Signal], exogenous_confirmed: bool) -> int:
    """Rule 4: decomposable strength score, 1-5. Starts from the
    strongest single signal's magnitude, +1 per additional confirming
    domain (rule 2), +1 if exogenous alignment confirms it (rule 3) --
    capped at 5. 'Decomposable' means each contributing factor is visible
    in the inputs here, not hidden in a single opaque number."""
    if not signals:
        return 0
    base = max(1, round(max(s.magnitude for s in signals) * 3))
    domain_bonus = max(0, len({s.domain for s in signals}) - 1)
    exogenous_bonus = 1 if exogenous_confirmed else 0
    return min(5, base + domain_bonus + exogenous_bonus)


def assemble(
    prty_id: str,
    signals: list[Signal],
    *,
    as_of: date,
    high_risk_flag: bool,
    exogenous_event_type: str | None = None,
    exogenous_event_source: str | None = None,
    exogenous_event_date: date | None = None,
    exogenous_event_value_eur: float | None = None,
    sizing: EndogenousSizing | None = None,
) -> Recommendation | None:
    """Returns None if there is nothing to recommend (no signals, or
    everything got suppressed). `high_risk_flag` is PARTY.HIGH_RSK_CUST_IND
    -- per docs/decision_record.md's "the one permitted read": it may
    ONLY be used here to suppress a revenue-category recommendation. It
    must never appear in crm_text/hypothesis, never scale
    business_value_score/signal_strength, and never be treated as a
    Signal on the bus. Enforced structurally below (it's a bool input,
    not a field on Recommendation, and touches only the category-gating
    branch)."""
    # As-of correctness at the assembler level too: never let a signal
    # observed after the run's as_of date leak into a recommendation --
    # a detector bug upstream shouldn't be the only thing standing
    # between this layer and future leakage.
    signals = [s for s in signals if s.observed_date <= as_of]
    if not signals:
        return None

    strongest = max(signals, key=lambda s: s.magnitude)
    category = _category_for(strongest.signal_type)

    # Rule 1: RISK_REVIEW / high-risk flag suppresses revenue categories.
    if high_risk_flag and category in REVENUE_CATEGORIES:
        category = "ADVISORY_ONLY"
    if any(_category_for(s.signal_type, default=None) == "RISK_REVIEW" for s in signals) \
            and category in REVENUE_CATEGORIES:
        category = "ADVISORY_ONLY"

    confirming_domains = tuple(sorted({s.domain for s in signals}))
    exogenous_confirmed = exogenous_event_type is not None
    if exogenous_confirmed:
        confirming_domains = tuple(sorted(set(confirming_domains) | {"exogenous"}))

    strength = _decomposable_strength(signals, exogenous_confirmed)
    if strength == 0:
        return None

    hypothesis = _hypothesis_for(strongest.signal_type)
    if exogenous_confirmed and exogenous_event_type == "public_tender_award":
        hypothesis = ("Winning a public tender creates a cash-flow gap between delivery and "
                      "payment, confirmed here by the client's own recent revenue growth -- "
                      "working capital sized to the contract is timely before delivery starts.")

    # Sizing: reuse the existing tender-value heuristic (worklist.py) when
    # an exogenous event with a real value confirms this signal. Otherwise
    # this pass has no revenue figure on FDM PARTY to size a %-of-revenue
    # offer against (that field lives only in the legacy schema) -- state
    # that honestly rather than inventing a number.
    sizing = sizing or EndogenousSizing()
    natural_category = _category_for(strongest.signal_type)
    sized_offer_eur = None
    if category in REVENUE_CATEGORIES and exogenous_confirmed and exogenous_event_value_eur:
        # Only a revenue category gets an offer -- a suppressed client must
        # never be offered financing just because an event matched.
        sized_offer_eur = round(exogenous_event_value_eur * TENDER_ADVANCE_PCT, 2)
        sizing_basis = f"{int(TENDER_ADVANCE_PCT * 100)}pct_of_event_value_illustrative"
        recommended_action = (
            f"Offer working-capital financing of ~EUR {sized_offer_eur:,.0f} "
            f"({int(TENDER_ADVANCE_PCT * 100)}% of the EUR {exogenous_event_value_eur:,.0f} "
            f"tender value, illustrative) to bridge delivery before payment."
        )
    elif category in REVENUE_CATEGORIES:
        sized_offer_eur, sizing_basis, recommended_action = _size_endogenous(strongest, sizing)
    elif natural_category in REVENUE_CATEGORIES:
        sizing_basis = "not_sized_suppressed_category"
        recommended_action = SUPPRESSED_ACTION
    else:
        sizing_basis = "not_sized_non_revenue_category"
        recommended_action = NON_REVENUE_ACTION.get(
            strongest.signal_type, "RM to review this signal -- no product offer.")

    crm_text = (f"{strongest.signal_type.replace('_', ' ').title()} observed. "
                f"{recommended_action}")[:200]  # Tab 4's hard 200-char limit

    # Deterministic, not random -- the same client+signal+evidence on a
    # rerun yields the same id, which is the whole point (a feedback
    # response has to join back to THIS recommendation, not a new one
    # minted every run). Not a security hash; truncated for readability.
    recommendation_id = hashlib.sha256(
        f"{prty_id}|{category}|{strongest.signal_type}|{strongest.evidence_ref}".encode()
    ).hexdigest()[:20]

    return Recommendation(
        prty_id=prty_id,
        nba_category=category,
        crm_text=crm_text,
        hypothesis=hypothesis,
        recommended_action=recommended_action,
        business_value_score=min(1000, 200 + strength * 150),  # illustrative 0-1000 scale,
                                                                  # NOT calibrated against Pega's
                                                                  # real Business Value scale
        confirming_domains=confirming_domains,
        signal_strength=strength,
        endogenous_signal_type=strongest.signal_type,
        exogenous_event_type=exogenous_event_type,
        exogenous_event_source=exogenous_event_source,
        exogenous_event_date=exogenous_event_date.strftime("%d/%m/%Y") if exogenous_event_date else None,
        evidence_ref=strongest.evidence_ref,
        sizing_basis=sizing_basis,
        raw_event_value_eur=exogenous_event_value_eur,
        sized_offer_eur=sized_offer_eur,
        recommendation_id=recommendation_id,
        baseline_source=(strongest.raw_measure or {}).get("baseline", "deterministic"),
        ambiguous=_is_ambiguous(strongest.signal_type),
    )
