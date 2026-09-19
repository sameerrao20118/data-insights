"""
Phase 1 proof, third schema (docs/generalization_plan.md): the SAME
detector tool factories in agents/tools.py that run against `fdm` and
`legacy` also run, unchanged, against `sba` -- REAL U.S. Small Business
Administration PPP loan entities/sectors/loans
(data_generator/fdm/load_sba.py), with synthetic deposit activity
layered on top, once routed through CanonicalSource +
config/bindings/sba.yaml.

Unlike `legacy` (synthetic throughout, chosen for structural
difference), this schema's PROOF is that real, sector-diverse commercial
data -- not retail, not synthetic firmographics -- drives the same
detectors with zero code changes. The sector diversity assertion below
checks against the real NAICS codes actually sampled, not an invented
list.

The sba binding deliberately does not provide RiskGradeVersion,
PartyMetricVersion, or CollateralValuation (config/bindings/sba.yaml) --
so risk/collateral-dependent tools must degrade honestly
(not_available_under_this_binding), never crash.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import pytest
import yaml

from agents.tools import make_deposits_tools, make_lending_tools, make_risk_tools
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.offline_local import OfflineLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBA_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm_sba")
SBA_CONTRACT = os.path.join(REPO_ROOT, "config", "entities_sba.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(SBA_DIR),
    reason="data_generator/output_fdm_sba/ not generated -- run "
           "`python -m data_generator.external.fetch_sba` then "
           "`python -m data_generator.fdm.load_sba` first",
)


@pytest.fixture(scope="module")
def canonical():
    return CanonicalSource(OfflineLocalSource(SBA_DIR, SBA_CONTRACT), load_binding("sba"))


@pytest.fixture(scope="module")
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def all_party_ids():
    """Scoped to a sample, matching test_legacy_binding_end_to_end.py's
    own discipline -- proving the same detector code runs against a
    third, real-data schema, not an exhaustive sweep."""
    ids = sorted(pd.read_csv(os.path.join(SBA_DIR, "party.csv"))["party_id"].unique())
    return ids[:40]


def test_sba_binding_validates_clean():
    from datainsights.semantic.validate import validate_binding
    assert validate_binding(load_binding("sba")) == []


def test_party_sectors_are_genuinely_diverse_real_naics_codes(canonical):
    """The whole point of choosing SBA over Berka: real sector spread,
    not one retail vertical. Checked against the actual data, not an
    assumption about it."""
    party = canonical.read("Party")
    assert len(party) > 0
    assert party["sector_code"].nunique() >= 10, (
        f"expected broad real sector diversity, got {party['sector_code'].nunique()} "
        f"distinct NAICS codes: {sorted(party['sector_code'].unique())}"
    )


def test_deposits_tools_run_against_every_sba_party_without_error(canonical, rules, all_party_ids):
    tools = make_deposits_tools(canonical, rules, date(2026, 3, 1))
    for prty_id in all_party_ids:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            assert "status" in result


def test_lending_tools_run_against_every_sba_party_without_error(canonical, rules, all_party_ids):
    tools = make_lending_tools(canonical, rules, date(2026, 3, 1))
    for prty_id in all_party_ids:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            assert "status" in result


def test_at_least_one_sba_party_has_a_detected_deposits_signal(canonical, rules, all_party_ids):
    """Proves this isn't just error-free plumbing -- real detections fire
    against the synthetic deposit activity layered onto these real
    entities."""
    tools = make_deposits_tools(canonical, rules, date(2026, 3, 1))
    any_detected = any(
        t(prty_id).get("status") == "detected"
        for prty_id in all_party_ids for t in tools
    )
    assert any_detected


def test_risk_tools_degrade_honestly_not_crash(canonical, rules, all_party_ids):
    """config/bindings/sba.yaml marks RiskGradeVersion/CollateralValuation
    unavailable (the real SBA release has no continuous rating history
    and PPP loans aren't collateralised) -- risk-domain tools must report
    that status, never raise and never fabricate a rating."""
    tools = make_risk_tools(canonical, rules, date(2026, 3, 1))
    seen_statuses = set()
    for prty_id in all_party_ids[:10]:
        for t in tools:
            result = t(prty_id)
            assert isinstance(result, dict)
            seen_statuses.add(result.get("status"))
    assert "not_available_under_this_binding" in seen_statuses
    assert "detected" not in seen_statuses
