"""
Hypothesis Assembler -- docs/decision_record.md Tab 3's four composition
rules, implemented verbatim:

  1. RISK_REVIEW suppresses revenue-category signals
  2. multi-domain confirmation curve
  3. exogenous alignment
  4. decomposable strength score

Produces a Recommendation carrying exactly the insight_attributes keys
from docs/decision_record.md Tab 4 (the MIMO insight record shape).

An exogenous event's hypothesis override and sizing behaviour come from
config/event_types.yaml's `correlation` block (via external_events/
event_registry.py, docs/generalization_plan.md Phase 2) -- this module
has no per-event-type branch; adding a new event type that confirms a
recommendation is a YAML edit there, not a change here.

This module's own job is the NEW part beyond a single detection: combining
multiple domain signals (and, now, a matched exogenous event) for one
client into one ranked, sized recommendation.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from datainsights import category_registry as _category_registry
from datainsights.domain_registry import category_for as _category_for
from datainsights.domain_registry import matching_combination as _matching_combination
from datainsights.domain_registry import non_revenue_action_for as _non_revenue_action_for
from datainsights.domain_registry import hypothesis_for as _hypothesis_for
from datainsights.domain_registry import is_ambiguous as _is_ambiguous
from detection_engine.signal import Signal
from external_events import event_registry

# Endogenous signal_type -> NBA category, and -> the general reasoning
# sentence, now live in config/domains_fdm.yaml (one block per domain),
# not here -- see datainsights/domain_registry.py's docstring for why
# this module reads them directly rather than via a passed-in rules dict.
# docs/adding_a_new_domain.md is the checklist for adding a signal_type's
# mapping there.

# R2: "is this a revenue category?" is answered by config/categories.yaml
# (revenue_model != none) at call time -- never a literal set, and never
# frozen at import, so a category added in YAML is live without a restart.
def _is_revenue(category: str) -> bool:
    return _category_registry.is_revenue(category)


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
    # R17: the currency every amount on this recommendation is in -- the
    # strongest signal's account currency (or the event's, when an exogenous
    # event sized the offer). Never assumed EUR.
    currency: str = "EUR"
    # R10: the name of the cross-domain rule (config/domains_*.yaml
    # `combinations:`) that set this category, or None when the strongest
    # single signal did -- so the Trace tab can show WHY two signals
    # together meant something one alone did not.
    combination_rule: str | None = None
    # R9: the Tier-2 investigator's proposal, attached by
    # agents/orchestrator.evaluate_client when narrating an ambiguous or
    # multi-domain recommendation. A PROPOSAL only -- nba_category above
    # is never changed by it. Keys: proposed_category, status, reasoning,
    # evidence_refs, could_not_determine, narrative_source.
    investigation: dict | None = None


@dataclass(frozen=True)
class EndogenousSizing:
    """Offer sizing for endogenous-only signals. Defaults mirror
    config/rules.yaml's `fdm_endogenous_sizing`; callers with the rules
    dict should use from_rules_dict so review-time edits take effect."""
    target_utilization_pct: float = 0.70
    min_offer_eur: float = 10_000
    # R17: per-currency floors; a currency not listed uses min_offer_eur.
    min_offer_by_currency: dict = field(default_factory=dict)

    def min_offer(self, currency: str) -> float:
        return float(self.min_offer_by_currency.get(str(currency).upper(), self.min_offer_eur))

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "EndogenousSizing":
        d = rules.get("fdm_endogenous_sizing", {})
        return cls(
            target_utilization_pct=d.get("target_utilization_pct", cls.target_utilization_pct),
            min_offer_eur=d.get("min_offer_eur", cls.min_offer_eur),
            min_offer_by_currency=d.get("min_offer_by_currency") or {},
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

# R2: non-revenue action text lives per signal in config/domains_*.yaml;
# the suppressed-category action lives in config/categories.yaml.


def _money(value: float, currency: str = "EUR") -> str:
    return f"{str(currency).upper()} {float(value):,.0f}"


def _size_endogenous(signal: Signal, sizing: EndogenousSizing) -> tuple[float | None, str, str]:
    """(sized_offer_eur, sizing_basis, recommended_action) sized from the
    client's own figures carried on the Signal, or an honest unsized
    action. Any missing/malformed figure yields unsized, never a guess."""
    m = signal.raw_measure or {}
    kind = signal.signal_type
    cur = str(m.get("currency", "EUR")).upper()
    floor = sizing.min_offer(cur)
    try:
        if kind == "cash_buildup":
            surplus = float(m["current_balance"]) - float(m["prior_balance"])
            if surplus >= floor:
                return (round(surplus, 2), "balance_buildup_amount_illustrative",
                        f"Offer a deposit or short-term investment placement for ~{_money(surplus, cur)} "
                        f"(the balance build-up over the review window, illustrative).")
        elif kind == "large_incoming_payment":
            surplus = float(m["flagged_amount"]) - float(m["baseline_median"])
            if surplus >= floor:
                return (round(surplus, 2), "payment_above_own_baseline_illustrative",
                        f"Offer a short-term deposit or investment placement for ~{_money(surplus, cur)} "
                        f"(the payment above this client's own baseline, illustrative).")
        elif kind == "facility_utilization_spike":
            drawn, limit = float(m["drawn_amount"]), float(m["orig_limit"])
            increase = drawn / sizing.target_utilization_pct - limit
            if limit > 0 and increase >= floor:
                return (round(increase, 2),
                        f"limit_increase_to_{round(sizing.target_utilization_pct * 100)}pct_utilization_illustrative",
                        f"Offer a facility limit increase of ~{_money(increase, cur)} to bring utilisation back "
                        f"to {sizing.target_utilization_pct:.0%} (currently {drawn / limit:.0%} of "
                        f"{_money(limit, cur)}, illustrative).")
        elif kind == "facility_maturity_approaching":
            limit = float(m["orig_limit"])
            if limit >= floor:
                return (round(limit, 2), "renewal_at_current_limit_illustrative",
                        f"Offer renewal of the {_money(limit, cur)} facility before it matures on "
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
    hypothesis = _hypothesis_for(strongest.signal_type)
    sizing_signal = strongest

    # R10: a cross-domain rule (config/domains_*.yaml `combinations:`)
    # consulted BEFORE strongest-signal-wins -- two signals together can
    # mean something one alone does not. No matching rule: unchanged.
    combination = _matching_combination({s.signal_type for s in signals})
    combination_rule = None
    if combination is not None:
        combination_rule = combination.name
        category = combination.category
        hypothesis = combination.hypothesis
        if combination.size_from:
            sizing_signal = next((s for s in signals if s.signal_type == combination.size_from), strongest)
    natural_category = category  # what the evidence says, before governance

    # Rule 1: RISK_REVIEW / high-risk flag suppresses revenue categories.
    if high_risk_flag and _is_revenue(category):
        category = "ADVISORY_ONLY"
    if any(_category_for(s.signal_type, default=None) == "RISK_REVIEW" for s in signals) \
            and _is_revenue(category):
        category = "ADVISORY_ONLY"

    confirming_domains = tuple(sorted({s.domain for s in signals}))
    exogenous_confirmed = exogenous_event_type is not None
    if exogenous_confirmed:
        confirming_domains = tuple(sorted(set(confirming_domains) | {"exogenous"}))

    strength = _decomposable_strength(signals, exogenous_confirmed)
    if strength == 0:
        return None

    exogenous_correlation = None
    if exogenous_confirmed:
        exogenous_correlation = (event_registry.spec(exogenous_event_type).correlation or None)
        if exogenous_correlation and exogenous_correlation.get("hypothesis"):
            hypothesis = exogenous_correlation["hypothesis"]

    # Sizing: an exogenous event's own correlation.sizing block (config/
    # event_types.yaml) decides how a confirmed revenue-category signal
    # gets sized -- e.g. public_tender_award's pct_of_event_value. A type
    # with no usable sizing here (missing event value, or a
    # `not_sized_this_pass` basis, e.g. fx_rate_move) falls through to the
    # same endogenous sizing an unconfirmed signal would get: this pass
    # has no revenue figure on FDM PARTY to size a %-of-revenue offer
    # against for every case (that field lives only in the legacy
    # schema) -- state that honestly rather than inventing a number.
    sizing = sizing or EndogenousSizing()
    sized_offer_eur = None
    currency = str((sizing_signal.raw_measure or {}).get("currency")
                   or next((s.raw_measure.get("currency") for s in signals if (s.raw_measure or {}).get("currency")), "EUR")).upper()
    registry_sizing = (exogenous_correlation or {}).get("sizing") if exogenous_correlation else None
    if (_is_revenue(category) and exogenous_confirmed and exogenous_event_value_eur
            and registry_sizing and registry_sizing.get("basis") == "pct_of_event_value"):
        # Only a revenue category gets an offer -- a suppressed client must
        # never be offered financing just because an event matched.
        pct = registry_sizing["pct"]
        sized_offer_eur = round(exogenous_event_value_eur * pct, 2)
        currency = "EUR"  # the event value is EUR-denominated (event_types.yaml); disclosed, not assumed
        sizing_basis = f"{int(pct * 100)}pct_of_event_value_illustrative"
        recommended_action = registry_sizing["action_template"].format(
            sized_offer_eur=sized_offer_eur, pct_int=int(pct * 100), event_value=exogenous_event_value_eur,
        )
    elif _is_revenue(category):
        sized_offer_eur, sizing_basis, recommended_action = _size_endogenous(sizing_signal, sizing)
    elif _is_revenue(natural_category):
        sizing_basis = "not_sized_suppressed_category"
        recommended_action = _category_registry.suppressed_action()
    else:
        sizing_basis = "not_sized_non_revenue_category"
        recommended_action = _non_revenue_action_for(strongest.signal_type)

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
        combination_rule=combination_rule,
        currency=currency,
    )
