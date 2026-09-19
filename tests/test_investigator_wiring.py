"""
R9 (docs/refactor_plan.md) -- the investigator (agents/investigator_agent.py,
built in A2 and never called from a run path) is now Tier 2 of
evaluate_client(): on a NARRATED run whose recommendation is ambiguous
or confirmed by more than one domain, it proposes; the proposal lands on
Recommendation.investigation and never on nba_category. Deterministic
here via tests/_stub_model.py's FailingModel -- the live proof is
tests/test_investigator_agent.py's existing live test.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from agents import orchestrator
from agents.investigator_agent import investigation_options
from agents.orchestrator import evaluate_book, evaluate_client, needs_investigation
from datainsights.correlation.hypothesis import assemble
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from detection_engine.signal import Signal
from external_events.exposure_qualifier import load_events
from tests._stub_model import FailingModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
EVENTS = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")


def _sig(signal_type, domain, magnitude):
    return Signal(prty_id="P1", signal_type=signal_type, domain=domain, direction="increase",
                  magnitude=magnitude, observed_date=date(2024, 6, 1), evidence_ref=f"TEST:{signal_type}",
                  source_tables=("TEST",), raw_measure={})


def test_trigger_is_ambiguous_or_multi_domain_never_exogenous_alone():
    single = assemble("P1", [_sig("cash_buildup", "deposits", 3)], as_of=date(2024, 6, 30), high_risk_flag=False)
    assert not needs_investigation(single)
    two = assemble("P1", [_sig("cash_buildup", "deposits", 3), _sig("facility_maturity_approaching", "lending", 1)],
                   as_of=date(2024, 6, 30), high_risk_flag=False)
    assert needs_investigation(two)
    ambiguous = assemble("P1", [_sig("fixed_rate_expiry", "lending", 2)], as_of=date(2024, 6, 30), high_risk_flag=False)
    assert ambiguous.ambiguous and needs_investigation(ambiguous)
    exo_only = assemble("P1", [_sig("cash_buildup", "deposits", 3)], as_of=date(2024, 6, 30), high_risk_flag=False,
                        exogenous_event_type="public_tender_award", exogenous_event_source="TED",
                        exogenous_event_date=date(2024, 6, 1), exogenous_event_value_eur=1e6)
    assert "exogenous" in exo_only.confirming_domains and not needs_investigation(exo_only)


def test_multi_domain_options_are_the_confirming_signals_categories_never_free_text():
    signals = [_sig("cash_buildup", "deposits", 3), _sig("facility_maturity_approaching", "lending", 1)]
    rec = assemble("P1", signals, as_of=date(2024, 6, 30), high_risk_flag=False)
    opts = investigation_options(rec, signals)
    assert set(opts) == {"TREASURY_OPPORTUNITY", "FINANCING_NEED"}
    # every domain agreeing -> nothing to investigate
    agree = [_sig("cash_buildup", "deposits", 3), _sig("revenue_pattern_change", "deposits", 1)]
    assert investigation_options(assemble("P1", agree, as_of=date(2024, 6, 30), high_risk_flag=False), agree) == []


@pytest.mark.skipif(not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS)), reason="FDM data not generated")
def test_narrated_run_attaches_a_note_and_never_changes_the_category():
    rt = build_runtime("fdm_local")
    event = load_events(EVENTS)[0]
    as_of = event.event_date + timedelta(days=90)
    ids = sorted(CanonicalSource(rt.source, load_binding("fdm")).read("Party", as_at=as_of)["party_id"])
    book = evaluate_book(ids, source=rt.source, rules=rt.rules, as_of=as_of, event=event)
    # batch path: never investigates
    assert all(e.investigation is None for e in book)
    assert all(e.recommendation is None or e.recommendation.investigation is None for e in book)
    candidates = [e for e in book if e.recommendation is not None and needs_investigation(e.recommendation)]
    assert candidates, "the dev book must contain at least one ambiguous or multi-domain recommendation"
    target = candidates[0]

    narrated = evaluate_client(target.prty_id, source=rt.source, rules=rt.rules, as_of=as_of, event=event,
                               narrate=True, model=FailingModel())
    rec = narrated.recommendation
    assert rec.nba_category == target.recommendation.nba_category
    assert rec.investigation is not None and narrated.investigation is not None
    assert rec.investigation["status"] == "needs_review"  # FailingModel -> honest fallback, never a guess
    assert rec.investigation["proposed_category"] == rec.nba_category
    assert "investigation failed" in rec.investigation["could_not_determine"]


def test_investigate_is_not_called_when_nothing_triggers(monkeypatch):
    calls = []
    monkeypatch.setattr(orchestrator, "investigate", lambda *a, **k: calls.append(1))
    if not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS)):
        pytest.skip("FDM data not generated")
    rt = build_runtime("fdm_local")
    event = load_events(EVENTS)[0]
    as_of = event.event_date + timedelta(days=90)
    ids = sorted(CanonicalSource(rt.source, load_binding("fdm")).read("Party", as_at=as_of)["party_id"])
    book = evaluate_book(ids, source=rt.source, rules=rt.rules, as_of=as_of, event=event)
    quiet = next(e for e in book if e.recommendation is not None and not needs_investigation(e.recommendation))
    evaluate_client(quiet.prty_id, source=rt.source, rules=rt.rules, as_of=as_of, event=event,
                    narrate=True, model=FailingModel())
    assert calls == []
