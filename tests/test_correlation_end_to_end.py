"""
M6 end-to-end verification: real FdmLocalSource + real detectors + real
generated tender event, all the way through to one Recommendation --
not hand-built fixtures. This is the test that proves M1-M6 actually
compose, not just that each piece passes in isolation.
"""

import os
from datetime import date, timedelta

import pandas as pd
import pytest
import yaml

from agents.tools import make_deposits_tools, make_lending_tools
from datainsights.correlation.dedupe import dedupe
from datainsights.correlation.hypothesis import assemble
from datainsights.correlation.signal_bus import SignalBus
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.fdm_local import FdmLocalSource
from detection_engine import (
    cash_buildup, collateral_coverage_drop, dormancy, facility_maturity_approaching,
    facility_utilization_spike, fixed_rate_expiry, revenue_pattern_change,
)
from external_events.exposure_qualifier import load_events, qualifies

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")
EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")

pytestmark = pytest.mark.skipif(
    not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS_PATH)),
    reason="run the M1/M5 generators first",
)

AS_OF = date(2026, 8, 20)


def _run_all_detectors_for_party(source, rules, prty_id, as_of) -> list:
    """Directly re-runs each detector's detect() (not via the agent tools,
    to also capture insufficient_evidence/point-in-time families
    uniformly) and collects every 'detected' row's Signal."""
    signals = []

    deposits_tools = make_deposits_tools(source, rules, as_of)
    lending_tools = make_lending_tools(source, rules, as_of)
    detector_by_tool = {
        "check_cash_buildup": cash_buildup,
        "check_dormancy": dormancy,
        "check_revenue_pattern_change": revenue_pattern_change,
        "check_facility_utilization": facility_utilization_spike,
        "check_facility_maturity": facility_maturity_approaching,
        "check_fixed_rate_expiry": fixed_rate_expiry,
        "check_collateral_coverage": collateral_coverage_drop,
    }
    for t in deposits_tools + lending_tools:
        result = t(prty_id)
        if result.get("status") != "detected":
            continue
        module = detector_by_tool[t.tool_name]
        # Reconstruct a minimal row for to_signal() from the tool's return
        # dict -- the tool already ran the real detect(), this just
        # re-shapes its evidence into the row shape to_signal() expects.
        row = pd.Series({**result, "prty_id": prty_id})
        signals.append(module.to_signal(row))
    return signals


@pytest.fixture(scope="module")
def source():
    return CanonicalSource(FdmLocalSource(FDM_DIR, CONTRACT_PATH), load_binding("fdm"))


@pytest.fixture(scope="module")
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def party_ids():
    return sorted(pd.read_csv(os.path.join(FDM_DIR, "kernel", "party.csv"))["PRTY_ID"].unique())


def test_full_pipeline_produces_recommendations_for_the_generated_book(source, rules, party_ids):
    bus = SignalBus()
    for prty_id in party_ids:
        bus.publish_all(_run_all_detectors_for_party(source, rules, prty_id, AS_OF))

    party_df = pd.read_csv(os.path.join(FDM_DIR, "kernel", "party.csv"))
    current = party_df[party_df["EFFECTIVE_END_DT"].isna() | (party_df["EFFECTIVE_END_DT"] == "")]
    high_risk_by_party = current.set_index("PRTY_ID")["HIGH_RSK_CUST_IND"].eq("Y").to_dict()

    recommendations = []
    for prty_id in bus.all_party_ids():
        rec = assemble(prty_id, bus.signals_for(prty_id), as_of=AS_OF,
                        high_risk_flag=high_risk_by_party.get(prty_id, False))
        if rec:
            recommendations.append(rec)

    final = dedupe(recommendations)
    assert len(final) > 0, "expected at least one recommendation across the generated book"
    assert all(r.nba_category in
               {"FINANCING_NEED", "TREASURY_OPPORTUNITY", "HEDGING_NEED",
                "CAPEX_FINANCING", "RISK_REVIEW", "ADVISORY_ONLY"}
               for r in final)


def test_the_matched_tender_award_scenario_end_to_end(source, rules):
    """The specific pairing M5 constructed: PRTY00036 has a real
    revenue_pattern_change signal AND matches the tender award event on
    sector+exposure -- the assembled recommendation must show BOTH
    domains confirming, sized off the real tender value. Evaluated
    shortly after the event (the generator dates the tender 30 days
    before the revenue growth it's meant to explain), not at the
    module-level AS_OF used for the whole-book test above -- the two are
    deliberately different timeframes."""
    events = load_events(EVENTS_PATH)
    event = events[0]
    as_of = event.event_date + timedelta(days=90)

    signals = _run_all_detectors_for_party(source, rules, "PRTY00036", as_of)
    revenue_signals = [s for s in signals if s.signal_type == "revenue_pattern_change"]
    assert revenue_signals, "expected PRTY00036 to have a real revenue_pattern_change detection"

    qualifies_result, _ = qualifies("PRTY00036", event, source, rules, as_of)
    assert qualifies_result

    rec = assemble(
        "PRTY00036", signals, as_of=as_of, high_risk_flag=False,
        exogenous_event_type=event.event_type, exogenous_event_source="TED",
        exogenous_event_date=event.event_date, exogenous_event_value_eur=event.estimated_value_eur,
    )
    assert rec.nba_category == "FINANCING_NEED"
    assert "exogenous" in rec.confirming_domains
    assert "deposits" in rec.confirming_domains
    assert "illustrative" in rec.recommended_action
    assert rec.exogenous_event_type == "public_tender_award"


def test_the_unexposed_client_produces_no_exogenous_confirmed_recommendation(source, rules):
    """PRTY00037 (same sector/country, no genuine revenue signal) must
    not receive an exogenous-confirmed recommendation -- the negative
    case, end to end."""
    events = load_events(EVENTS_PATH)
    event = events[0]
    as_of = event.event_date + timedelta(days=90)
    qualifies_result, _ = qualifies("PRTY00037", event, source, rules, as_of)
    assert qualifies_result is False
