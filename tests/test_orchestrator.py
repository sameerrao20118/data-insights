"""
Tests for agents/orchestrator.py -- the agentic pipeline as one callable.

The decisive test is test_narration_never_changes_the_recommendation: the
same client produces the IDENTICAL Recommendation whether the domain
agents make LLM calls or not. That is the mechanical proof of the
architecture's central claim -- the LLM narrates, it never decides.
Uses tests/_stub_model.FailingModel, so no network and no Ollama needed.
"""

import os
from datetime import timedelta

import pytest
import yaml

from agents.orchestrator import evaluate_book, evaluate_client, signals_from_tool_evidence
from datainsights.sources.fdm_local import FdmLocalSource
from external_events.exposure_qualifier import load_events
from tests._stub_model import FailingModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")
EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")

pytestmark = pytest.mark.skipif(
    not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS_PATH)),
    reason="run the FDM data + event generators first",
)


@pytest.fixture(scope="module")
def source():
    return FdmLocalSource(FDM_DIR, CONTRACT_PATH)


@pytest.fixture(scope="module")
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def event():
    return load_events(EVENTS_PATH)[0]


@pytest.fixture(scope="module")
def as_of(event):
    return event.event_date + timedelta(days=90)


def test_exposed_client_gets_cross_domain_recommendation(source, rules, event, as_of):
    ev = evaluate_client("PRTY00036", source=source, rules=rules, as_of=as_of,
                          event=event, narrate=False)
    assert ev.exogenous_confirmed
    assert ev.recommendation is not None
    assert ev.recommendation.nba_category == "FINANCING_NEED"
    assert set(ev.recommendation.confirming_domains) >= {"deposits", "exogenous"}
    assert ev.recommendation.sized_offer_eur == pytest.approx(event.estimated_value_eur * 0.25)
    # "risk" joined this set once A4 registered it (agents/tools.py's
    # make_risk_tools) -- every registered non-exogenous domain always
    # evaluates every client, same as deposits/lending always have.
    assert set(ev.agent_results) == {"deposits", "lending", "exogenous", "risk"}


def test_unexposed_client_gets_no_exogenous_confirmation(source, rules, event, as_of):
    ev = evaluate_client("PRTY00037", source=source, rules=rules, as_of=as_of,
                          event=event, narrate=False)
    assert not ev.exogenous_confirmed
    if ev.recommendation is not None:
        assert ev.recommendation.exogenous_event_type is None


def test_narration_never_changes_the_recommendation(source, rules, event, as_of):
    """Same client, same data, LLM on (stub model, forced fallback) vs
    LLM off -- the Recommendation must be identical field for field."""
    silent = evaluate_client("PRTY00036", source=source, rules=rules, as_of=as_of,
                              event=event, narrate=False)
    narrated = evaluate_client("PRTY00036", source=source, rules=rules, as_of=as_of,
                                event=event, model=FailingModel(), narrate=True)
    assert silent.recommendation == narrated.recommendation


def test_narrate_true_without_model_is_rejected(source, rules, as_of):
    with pytest.raises(ValueError, match="requires a model"):
        evaluate_client("PRTY00036", source=source, rules=rules, as_of=as_of, narrate=True)


def test_without_event_there_is_no_exogenous_agent(source, rules, as_of):
    ev = evaluate_client("PRTY00036", source=source, rules=rules, as_of=as_of, narrate=False)
    assert "exogenous" not in ev.agent_results
    assert not ev.exogenous_confirmed


def test_evaluate_book_runs_every_client_in_batch_mode(source, rules, as_of):
    import pandas as pd
    ids = sorted(pd.read_csv(os.path.join(FDM_DIR, "kernel", "party.csv"))["PRTY_ID"].unique())[:10]
    results = evaluate_book(ids, source=source, rules=rules, as_of=as_of)
    assert [r.prty_id for r in results] == ids


def test_signals_ignore_undetected_and_exogenous_evidence():
    evidence = {
        "check_cash_buildup": {"status": "not_detected"},
        "check_exogenous_exposure": {"status": "detected", "event_type": "public_tender_award"},
    }
    assert signals_from_tool_evidence("P1", evidence) == []
