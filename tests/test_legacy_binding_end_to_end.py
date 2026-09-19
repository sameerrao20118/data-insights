"""
Phase 1 rewire proof (docs/generalization_plan.md): the SAME detector
tool factories in agents/tools.py that run against the FDM schema also
run, unchanged, against the legacy schema (config/entities.yaml,
data_generator/output/) once routed through CanonicalSource +
config/bindings/legacy.yaml. This is the actual "any schema" claim --
not a unit test of the semantic layer in isolation, but proof that a
second, structurally different physical schema drives the real
detectors with zero changes to agents/tools.py or detection_engine/.

The legacy binding deliberately does not provide RiskGradeVersion,
PartyMetricVersion, or CollateralValuation (config/bindings/legacy.yaml)
-- so risk/collateral-dependent tools must degrade honestly
(not_available_under_this_binding), never crash.
"""

from __future__ import annotations

import os
from datetime import date

import pytest
import yaml

from agents.tools import make_deposits_tools, make_lending_tools, make_risk_tools
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.offline_local import OfflineLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_DIR = os.path.join(REPO_ROOT, "data_generator", "output")
LEGACY_CONTRACT = os.path.join(REPO_ROOT, "config", "entities.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(LEGACY_DIR),
    reason="data_generator/output/ not generated -- run `python -m data_generator.generate_data` first",
)


@pytest.fixture(scope="module")
def canonical():
    return CanonicalSource(OfflineLocalSource(LEGACY_DIR, LEGACY_CONTRACT), load_binding("legacy"))


@pytest.fixture(scope="module")
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def all_party_ids():
    """Scoped to a sample, not the full 606-account legacy book -- the
    point here is proving the SAME detector code runs against a second
    schema at all, not an exhaustive sweep (that's what the FDM-schema
    tests in tests/test_agent_tools.py already do, at that dataset's
    smaller scale)."""
    import pandas as pd
    ids = sorted(pd.read_csv(os.path.join(LEGACY_DIR, "accounts.csv"))["client_id"].unique())
    return ids[:40]


def test_legacy_binding_validates_clean():
    from datainsights.semantic.validate import validate_binding
    assert validate_binding(load_binding("legacy")) == []


def test_deposits_tools_run_against_every_legacy_party_without_error(canonical, rules, all_party_ids):
    tools = make_deposits_tools(canonical, rules, date(2025, 10, 4))
    for prty_id in all_party_ids:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            assert "status" in result


def test_lending_tools_run_against_every_legacy_party_without_error(canonical, rules, all_party_ids):
    tools = make_lending_tools(canonical, rules, date(2025, 10, 4))
    for prty_id in all_party_ids:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            assert "status" in result


def test_at_least_one_legacy_party_has_a_detected_deposits_signal(canonical, rules, all_party_ids):
    """Proves this isn't just error-free plumbing -- real detections fire
    against the legacy dataset's own transaction/balance history."""
    tools = make_deposits_tools(canonical, rules, date(2025, 10, 4))
    any_detected = any(
        t(prty_id).get("status") == "detected"
        for prty_id in all_party_ids for t in tools
    )
    assert any_detected


def test_risk_tools_degrade_honestly_not_crash(canonical, rules, all_party_ids):
    """config/bindings/legacy.yaml marks RiskGradeVersion/CollateralValuation
    unavailable -- risk-domain tools must report that status, never raise
    and never fabricate a rating."""
    tools = make_risk_tools(canonical, rules, date(2025, 10, 4))
    seen_statuses = set()
    for prty_id in all_party_ids[:10]:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            seen_statuses.add(result.get("status"))
    assert "not_available_under_this_binding" in seen_statuses
    assert "detected" not in seen_statuses
