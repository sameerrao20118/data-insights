"""
M6 verification for datainsights/correlation/: the signal bus, the
hypothesis assembler's 4 composition rules, and de-duplication.

The two most important tests here are test_high_risk_flag_suppresses_revenue_category
and test_risk_review_signal_suppresses_revenue_category -- proving
docs/decision_record.md's "the one permitted read" boundary (observe +
suppress only, never rank/correlate/narrate) is actually enforced in
code, not just documented.
"""

from datetime import date

import pytest

from datainsights.correlation.dedupe import dedupe
from datainsights.correlation.hypothesis import Recommendation, assemble
from datainsights.correlation.signal_bus import SignalBus
from detection_engine.signal import Signal


def make_signal(prty_id="P1", signal_type="cash_buildup", domain="deposits",
                 magnitude=0.5, observed_date=date(2026, 1, 1)):
    return Signal(
        prty_id=prty_id, signal_type=signal_type, domain=domain, direction="increase",
        magnitude=magnitude, observed_date=observed_date,
        evidence_ref=f"TEST:{prty_id}", source_tables=("TEST",), raw_measure={"x": 1},
    )


# --- SignalBus ---------------------------------------------------------

def test_signal_bus_groups_by_party():
    bus = SignalBus()
    bus.publish_all([make_signal("P1"), make_signal("P2"), make_signal("P1", domain="lending")])
    assert set(bus.all_party_ids()) == {"P1", "P2"}
    assert len(bus.signals_for("P1")) == 2
    assert bus.domains_present_for("P1") == {"deposits", "lending"}


# --- Hypothesis Assembler: basic shape ----------------------------------

def test_no_signals_returns_none():
    assert assemble("P1", [], as_of=date(2026, 1, 1), high_risk_flag=False) is None


def test_single_signal_produces_a_recommendation():
    sig = make_signal(signal_type="cash_buildup")
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec is not None
    assert rec.nba_category == "TREASURY_OPPORTUNITY"
    assert rec.confirming_domains == ("deposits",)
    assert len(rec.crm_text) <= 200


def test_as_of_excludes_future_signals():
    """A signal observed after as_of must not leak into the recommendation
    -- assembler-level as-of correctness, independent of any upstream
    detector bug."""
    future_signal = make_signal(observed_date=date(2027, 1, 1))
    rec = assemble("P1", [future_signal], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec is None


# --- Rule 2: multi-domain confirmation curve ----------------------------

def test_multiple_domains_increase_strength_over_single_domain():
    single = assemble("P1", [make_signal(domain="deposits", magnitude=0.3)],
                       as_of=date(2026, 1, 1), high_risk_flag=False)
    multi = assemble("P1", [make_signal(domain="deposits", magnitude=0.3),
                             make_signal(domain="lending", signal_type="facility_utilization_spike", magnitude=0.3)],
                      as_of=date(2026, 1, 1), high_risk_flag=False)
    assert multi.signal_strength > single.signal_strength
    assert set(multi.confirming_domains) == {"deposits", "lending"}


# --- Rule 3: exogenous alignment ----------------------------------------

def test_exogenous_confirmation_increases_strength_and_sizes_the_offer():
    sig = make_signal(signal_type="revenue_pattern_change", domain="deposits", magnitude=0.6)
    without_exo = assemble("P1", [sig], as_of=date(2026, 8, 20), high_risk_flag=False)
    with_exo = assemble(
        "P1", [sig], as_of=date(2026, 8, 20), high_risk_flag=False,
        exogenous_event_type="public_tender_award", exogenous_event_source="TED",
        exogenous_event_date=date(2026, 8, 14), exogenous_event_value_eur=3_200_000,
    )
    assert with_exo.signal_strength >= without_exo.signal_strength
    assert "exogenous" in with_exo.confirming_domains
    assert with_exo.exogenous_event_type == "public_tender_award"
    assert with_exo.exogenous_event_date == "14/08/2026"
    assert "illustrative" in with_exo.recommended_action
    assert "800,000" in with_exo.recommended_action.replace(",", ",")  # 25% of 3.2M


def test_without_exogenous_event_sizing_is_disclosed_as_not_sized():
    sig = make_signal(signal_type="cash_buildup")
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec.sizing_basis == "not_sized_this_pass_no_revenue_figure_on_fdm_party"


# --- Rule 1: RISK_REVIEW / HIGH_RSK_CUST_IND suppression ----------------

def test_high_risk_flag_suppresses_revenue_category():
    """docs/decision_record.md: HIGH_RSK_CUST_IND may be OBSERVED and used
    to SUPPRESS a revenue recommendation. Here: a cash_buildup signal
    (normally TREASURY_OPPORTUNITY) on a high-risk-flagged client must
    downgrade to ADVISORY_ONLY."""
    sig = make_signal(signal_type="cash_buildup")
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=True)
    assert rec.nba_category == "ADVISORY_ONLY"


def test_high_risk_flag_never_appears_in_narrative_text():
    """The flag must never be surfaced in crm_text or hypothesis -- it is
    observe-and-suppress only, never narrated."""
    sig = make_signal(signal_type="cash_buildup")
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=True)
    combined = (rec.crm_text + " " + rec.hypothesis).lower()
    for banned in ("high risk", "high_rsk", "risk flag", "flagged as risk"):
        assert banned not in combined


def test_high_risk_flag_does_not_scale_strength_or_score():
    """The flag gates the CATEGORY only -- it must not be used to inflate
    or deflate signal_strength/business_value_score, which are derived
    purely from signal magnitude/domain confirmation."""
    sig = make_signal(signal_type="dormancy", magnitude=0.5)  # already maps to ADVISORY_ONLY
    without_flag = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    with_flag = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=True)
    assert without_flag.signal_strength == with_flag.signal_strength
    assert without_flag.business_value_score == with_flag.business_value_score


def test_risk_review_signal_suppresses_a_revenue_signal_on_the_same_client():
    """A RISK_REVIEW-mapped signal (collateral_coverage_drop) present
    alongside a revenue-category signal for the same client suppresses
    the revenue category -- rule 1 applied via a genuine Signal, not just
    the PARTY flag."""
    revenue_sig = make_signal(signal_type="facility_utilization_spike", domain="lending", magnitude=0.9)
    risk_sig = make_signal(signal_type="collateral_coverage_drop", domain="lending", magnitude=0.3)
    rec = assemble("P1", [revenue_sig, risk_sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec.nba_category == "ADVISORY_ONLY"


# --- Dedupe --------------------------------------------------------------

def _rec(prty_id, category, strength):
    return Recommendation(
        prty_id=prty_id, nba_category=category, crm_text="x", hypothesis="x",
        recommended_action="x", business_value_score=100, confirming_domains=("deposits",),
        signal_strength=strength, endogenous_signal_type="cash_buildup",
        exogenous_event_type=None, exogenous_event_source=None, exogenous_event_date=None,
        evidence_ref="x", sizing_basis="x",
    )


def test_dedupe_keeps_strongest_within_run():
    recs = [_rec("P1", "TREASURY_OPPORTUNITY", 2), _rec("P1", "TREASURY_OPPORTUNITY", 4)]
    result = dedupe(recs)
    assert len(result) == 1
    assert result[0].signal_strength == 4


def test_dedupe_keeps_distinct_categories_separately():
    recs = [_rec("P1", "TREASURY_OPPORTUNITY", 2), _rec("P1", "FINANCING_NEED", 2)]
    result = dedupe(recs)
    assert len(result) == 2


class _AlwaysHasOpenItem:
    def has_open_item(self, prty_id, category):
        return True


def test_dedupe_respects_external_checker():
    recs = [_rec("P1", "TREASURY_OPPORTUNITY", 2)]
    result = dedupe(recs, checker=_AlwaysHasOpenItem())
    assert result == []


# --- Endogenous sizing (RM needs a number to prioritise and pitch) -----------

def _signal(signal_type, domain, raw_measure, magnitude=0.6):
    return Signal(
        prty_id="P1", signal_type=signal_type, domain=domain, direction="increase",
        magnitude=magnitude, observed_date=date(2026, 1, 1), evidence_ref="TEST:P1",
        source_tables=("TEST",), raw_measure=raw_measure,
    )


def test_cash_buildup_sized_as_surplus_placement():
    sig = _signal("cash_buildup", "deposits", {"current_balance": 150_000.0, "prior_balance": 100_000.0})
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec.nba_category == "TREASURY_OPPORTUNITY"
    assert rec.sized_offer_eur == 50_000.0
    assert rec.sizing_basis == "balance_buildup_amount_illustrative"
    assert "illustrative" in rec.recommended_action


def test_utilization_spike_sized_as_headroom_restoring_limit_increase():
    sig = _signal("facility_utilization_spike", "lending",
                  {"drawn_amount": 95_000.0, "orig_limit": 100_000.0, "utilization_pct": 0.95})
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec.nba_category == "FINANCING_NEED"
    # 95,000 / 0.70 - 100,000 = 35,714.29
    assert rec.sized_offer_eur == pytest.approx(35_714.29, abs=0.01)
    assert "70%" in rec.recommended_action and "95%" in rec.recommended_action


def test_maturity_sized_as_renewal_at_current_limit():
    sig = _signal("facility_maturity_approaching", "lending",
                  {"close_date": "2026-03-01", "days_to_close": 59, "orig_limit": 250_000.0})
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec.sized_offer_eur == 250_000.0
    assert rec.sizing_basis == "renewal_at_current_limit_illustrative"


def test_trivial_amount_stays_unsized_rather_than_pitched():
    sig = _signal("cash_buildup", "deposits", {"current_balance": 102_000.0, "prior_balance": 100_000.0})
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=False)
    assert rec.sized_offer_eur is None
    assert "{" not in rec.recommended_action


def test_suppressed_client_is_never_offered_anything():
    """Rule 1 applied to sizing: an otherwise-sizable signal AND a matching
    external event must still produce no offer once the category is
    suppressed -- and the reason must not leak into the text."""
    sig = _signal("cash_buildup", "deposits", {"current_balance": 500_000.0, "prior_balance": 100_000.0})
    rec = assemble("P1", [sig], as_of=date(2026, 1, 1), high_risk_flag=True,
                   exogenous_event_type="public_tender_award", exogenous_event_source="TED",
                   exogenous_event_date=date(2025, 12, 1), exogenous_event_value_eur=3_200_000)
    assert rec.nba_category == "ADVISORY_ONLY"
    assert rec.sized_offer_eur is None
    assert rec.sizing_basis == "not_sized_suppressed_category"
    assert "risk" not in rec.recommended_action.lower()


def test_non_revenue_actions_are_readable_sentences():
    for kind, domain in (("dormancy", "deposits"), ("collateral_coverage_drop", "lending")):
        rec = assemble("P1", [_signal(kind, domain, {"days_since_last_event": 120})],
                       as_of=date(2026, 1, 1), high_risk_flag=False)
        assert rec.sized_offer_eur is None
        assert "{" not in rec.recommended_action and "'" not in rec.recommended_action


def test_sizing_config_loads_from_rules_yaml():
    import os

    import yaml

    from datainsights.correlation.hypothesis import EndogenousSizing

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "rules.yaml")
    with open(path) as f:
        loaded = EndogenousSizing.from_rules_dict(yaml.safe_load(f))
    defaults = EndogenousSizing()  # scalar defaults mirror the reviewed config
    assert (loaded.target_utilization_pct, loaded.min_offer_eur) == (defaults.target_utilization_pct, defaults.min_offer_eur)
    assert loaded.min_offer_by_currency["EUR"] == loaded.min_offer_eur  # R17: EUR floor agrees with the scalar
