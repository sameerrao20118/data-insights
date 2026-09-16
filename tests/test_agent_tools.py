"""
M4 verification for agents/tools.py: the tool factories actually wire
FdmLocalSource + detection_engine correctly against the real generated
dataset (data_generator/output_fdm/), not just against hand-built frames.
This is the integration layer the per-detector unit tests don't cover.
"""

import os
from datetime import date

import pytest
import yaml

from agents.tools import make_deposits_tools, make_exogenous_tools, make_lending_tools
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(OUT_DIR),
    reason="data_generator/output_fdm/ not generated -- run "
           "`python -m data_generator.fdm.generate_fdm` first",
)


@pytest.fixture
def source():
    return FdmLocalSource(OUT_DIR, CONTRACT_PATH)


@pytest.fixture
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture
def all_party_ids(source):
    import pandas as pd
    return sorted(pd.read_csv(os.path.join(OUT_DIR, "kernel", "party.csv"))["PRTY_ID"].unique())


def test_deposits_tools_run_against_every_party_without_error(source, rules, all_party_ids):
    tools = make_deposits_tools(source, rules, date(2026, 3, 1))
    for prty_id in all_party_ids:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            assert "status" in result


def test_lending_tools_run_against_every_party_without_error(source, rules, all_party_ids):
    tools = make_lending_tools(source, rules, date(2026, 3, 1))
    for prty_id in all_party_ids:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            assert "status" in result


def test_at_least_one_party_has_a_detected_deposits_signal(source, rules, all_party_ids):
    """Confirms the wiring doesn't just run without error -- it actually
    finds real signals in the generated dataset, given the generator
    deliberately seeds cash_buildup/dormancy/revenue_pattern_change cases."""
    tools = make_deposits_tools(source, rules, date(2026, 3, 1))
    any_detected = False
    for prty_id in all_party_ids:
        for t in tools:
            if t(prty_id).get("status") == "detected":
                any_detected = True
    assert any_detected


def test_at_least_one_party_has_a_detected_lending_signal(source, rules, all_party_ids):
    tools = make_lending_tools(source, rules, date(2026, 6, 1))
    any_detected = False
    for prty_id in all_party_ids:
        for t in tools:
            if t(prty_id).get("status") == "detected":
                any_detected = True
    assert any_detected


def test_evidence_ref_present_on_every_detection(source, rules, all_party_ids):
    tools = make_deposits_tools(source, rules, date(2026, 3, 1)) + \
        make_lending_tools(source, rules, date(2026, 6, 1))
    for prty_id in all_party_ids:
        for t in tools:
            result = t(prty_id)
            if result.get("status") == "detected":
                assert "evidence_ref" in result and result["evidence_ref"]


def test_exogenous_tool_matches_the_generated_tender_scenario(source, rules):
    """Uses the real generated tender event and its known exposed/
    unexposed pair (see data_generator/fdm/generate_fdm_events.py)."""
    from datetime import timedelta
    from external_events.exposure_qualifier import load_events

    events_path = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")
    if not os.path.exists(events_path):
        pytest.skip("run `python -m data_generator.fdm.generate_fdm_events` first")
    event = load_events(events_path)[0]
    as_of = event.event_date + timedelta(days=90)
    tools = make_exogenous_tools(source, rules, event, as_of)
    assert len(tools) == 1
    check = tools[0]

    exposed = check("PRTY00036")
    assert exposed["status"] == "detected"
    assert exposed["event_type"] == "public_tender_award"
    assert 0 < exposed["exposure_magnitude"] <= 1

    unexposed = check("PRTY00037")
    assert unexposed["status"] == "not_detected"
