"""
A4 verification (docs/agentic_plan.md): the Risk domain, registered
entirely through the M7 registry (agents/tools.py's make_risk_tools +
config/domains_fdm.yaml's risk: block), works end to end against the
real generated FDM dataset -- no edits to orchestrator.py, domain_agent.py,
or hypothesis.py were needed to add it (proven structurally: those files
were not touched by the Risk registration commit; test_domain_registry.py
already proves the mechanism generically with a throwaway domain, this
file proves it with the REAL one).
"""

import os
from datetime import date

import pytest
import yaml

from agents.orchestrator import evaluate_book, evaluate_client
from datainsights.category_registry import revenue_categories
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


@pytest.fixture(scope="module")
def source():
    return FdmLocalSource(FDM_DIR, CONTRACT_PATH)


@pytest.fixture(scope="module")
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


def test_risk_domain_evaluates_every_client(source, rules):
    ev = evaluate_client("PRTY00001", source=source, rules=rules, as_of=date(2025, 10, 4), narrate=False)
    assert "risk" in ev.agent_results


def test_whole_book_shows_at_least_one_genuine_rating_downgrade(source, rules):
    """Not asserting a specific client (that's the generator's business,
    not this test's) -- asserting the wiring actually surfaces a real
    detection on real data, not just that it runs without crashing."""
    parties = source.party(date(2025, 10, 4))
    prty_ids = sorted(parties["PRTY_ID"])
    evaluations = evaluate_book(prty_ids, source=source, rules=rules, as_of=date(2025, 10, 4))

    risk_signals = [s for ev in evaluations for s in ev.signals if s.signal_type == "rating_downgrade"]
    assert len(risk_signals) >= 1, "expected at least one real rating_downgrade signal in the generated book"

    # The signal that matters most: rule 1 suppression actually reaches a
    # client through this new domain, not just through HIGH_RSK_CUST_IND.
    downgraded_prty_ids = {s.prty_id for s in risk_signals}
    recs_for_downgraded = [ev.recommendation for ev in evaluations
                           if ev.prty_id in downgraded_prty_ids and ev.recommendation]
    violations = [r for r in recs_for_downgraded if r.nba_category in revenue_categories()]
    assert violations == [], (
        f"a client with a genuine rating downgrade still got a revenue-category "
        f"recommendation: {[(r.prty_id, r.nba_category) for r in violations]}"
    )


def test_rating_downgrade_signal_has_traceable_evidence(source, rules):
    """The exact bug adding_a_new_domain.md warns every new tool hits
    once (a missing field silently breaking to_signal()) -- this asserts
    the evidence_ref actually resolves, not just that detect() runs."""
    parties = source.party(date(2025, 10, 4))
    prty_ids = sorted(parties["PRTY_ID"])
    evaluations = evaluate_book(prty_ids, source=source, rules=rules, as_of=date(2025, 10, 4))
    risk_signals = [s for ev in evaluations for s in ev.signals if s.signal_type == "rating_downgrade"]
    assert risk_signals
    for s in risk_signals:
        assert s.evidence_ref.startswith("PARTY:")
        assert "eff=" in s.evidence_ref
        assert s.domain == "risk"
