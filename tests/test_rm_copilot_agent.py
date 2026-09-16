"""
A3 verification for agents/rm_copilot_agent.py -- the answer must be
groundable in the row's own fields, and any failure degrades to a plain
fact list, never an unvalidated claim.
"""

from __future__ import annotations

import pytest

from agents.rm_copilot_agent import _validate, ask
from tests._stub_model import FailingModel

ROW = {
    "prty_id": "PRTY00036", "segment": "SME", "sector": "Manufacturing", "country": "ES",
    "nba_category": "FINANCING_NEED", "indicative_revenue_eur": 21_200.0,
    "indicative_offer_eur": 800_000.0, "why_now": "Incoming payment pattern has structurally shifted.",
    "hypothesis": "A structural step-change in incoming payment pattern often reflects growth.",
    "recommended_action": "Offer working-capital financing of ~EUR 800,000.",
    "talking_point": "We noticed your incoming payment pattern has shifted.",
    "confirming_domains": "deposits,exogenous", "signal_strength": 3,
    "endogenous_signal_type": "revenue_pattern_change", "exogenous_event_type": "public_tender_award",
    "exogenous_event_date": "06/07/2025", "evidence_ref": "EVENT_FINANCIAL:AGR000060:eff=2025-08-21",
    "sizing_basis": "25pct_of_event_value_illustrative", "response_actions": "Customer Engaged,Not Appropriate,Remind Me Later",
}


# --- deterministic validation ----------------------------------------------

def test_answer_grounded_in_row_numbers_passes():
    assert _validate("Sized at approximately EUR 800,000, 25% of the tender value.", ROW) == []


def test_answer_with_untraceable_number_rejected():
    problems = _validate("This client will generate EUR 5,000,000 in revenue.", ROW)
    assert any("not traceable" in p for p in problems)


def test_empty_answer_rejected():
    assert _validate("", ROW) == ["empty answer"]


def test_banned_term_rejected():
    problems = _validate("This relates to a sanctions review.", ROW)
    assert any("disallowed term" in p for p in problems)


def test_small_numbers_like_signal_strength_are_not_flagged():
    """'3/5' or '2 domains' shouldn't trip the traceability check just
    because 3 and 2 aren't literally row values -- small numbers are
    common restatements (ranks, counts), not fabricated figures."""
    assert _validate("Confidence is 3 out of 5, confirmed by 2 domains.", ROW) == []


# --- fallback discipline (no Ollama needed) ---------------------------------

def test_model_failure_falls_back_to_fact_list():
    answer = ask("Why this offer?", ROW, FailingModel())
    assert answer.status == "fallback"
    assert "prty_id" in answer.answer.lower() or "PRTY00036" in answer.answer
    assert "800,000" in answer.answer or "800000" in answer.answer.replace(",", "")


# --- Opt-in live test: real local Ollama ------------------------------------

def _ollama_reachable() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_copilot_answers_grounded_in_the_row():
    from agents.model_factory import ModelConfig, get_model

    model = get_model(ModelConfig(mode="local", model_id="qwen2.5:7b"))
    answer = ask("Why is this recommendation sized the way it is?", ROW, model)
    print(f"\nCopilot answer ({answer.status}): {answer.answer}")
    assert answer.answer.strip()
