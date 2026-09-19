"""
M4 verification for agents/domain_agent.py and agents/model_factory.py.

Runs against a stubbed model (tests/_stub_model.py) that always raises --
no network call, no running Ollama required -- so these tests confirm the
validate-or-fallback MECHANISM works, not that a specific model produces
good narratives. One separate, opt-in test at the bottom exercises the
real local Ollama server if it's reachable, skipped otherwise.
"""

from __future__ import annotations

import urllib.request

import pytest
from strands import tool

from agents.domain_agent import DomainAgent, DomainNarrative, _numeric_tolerance_ok
from agents.model_factory import ModelConfig, get_model
from tests._stub_model import FailingModel


@tool
def check_thing_detected(prty_id: str) -> dict:
    """A fake detector tool that always reports a detection."""
    return {"status": "detected", "agrmnt_id": "A1", "increase_pct": 0.42, "current_balance": 12345.0}


@tool
def check_thing_not_detected(prty_id: str) -> dict:
    """A fake detector tool that always reports no detection."""
    return {"status": "not_detected"}


def test_model_factory_local_uses_ollama_model():
    model = get_model(ModelConfig(mode="local"))
    from strands.models.ollama import OllamaModel
    assert isinstance(model, OllamaModel)


def test_model_factory_rejects_cloud_tag():
    with pytest.raises(ValueError, match="cloud"):
        get_model(ModelConfig(mode="local", model_id="qwen2.5:7b-cloud"))


def test_model_factory_rejects_non_localhost():
    with pytest.raises(ValueError, match="localhost"):
        get_model(ModelConfig(mode="local", base_url="http://example.com:11434"))


def test_model_factory_model_gateway_not_implemented():
    with pytest.raises(NotImplementedError, match="contract-only"):
        get_model(ModelConfig(mode="model_gateway"))


def test_agent_falls_back_when_model_raises():
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    result = agent.evaluate("PRTY00001")
    assert result.narrative_source.startswith("deterministic_template")
    assert "detected" in result.tool_evidence["check_thing_detected"]["status"]
    assert result.suggested_action in agent.allowed_actions


def test_fallback_action_is_monitor_only_when_nothing_detected():
    agent = DomainAgent(domain="deposits", tools=[check_thing_not_detected], model=FailingModel())
    result = agent.evaluate("PRTY00001")
    assert result.suggested_action == "No action -- monitor only"


def test_unknown_domain_rejected():
    with pytest.raises(ValueError, match="unknown domain"):
        DomainAgent(domain="not_a_real_domain", tools=[], model=FailingModel())


def test_validate_rejects_disallowed_action():
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts="Balance increased 42%, now 12345.0",
        hypothesis="Client may be accumulating cash.",
        suggested_action="Email the client immediately",  # not in allowed set
        caveats="test",
    )
    problems = agent._validate(parsed, {"check_thing_detected": {"status": "detected", "increase_pct": 0.42}})
    assert any("not in allowed set" in p for p in problems)


def test_validate_rejects_banned_fincrime_term():
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts="Balance increased 42%, now 12345.0. Possible sanctions concern.",
        hypothesis="Client may be accumulating cash.",
        suggested_action=agent.allowed_actions[0],
        caveats="test",
    )
    problems = agent._validate(parsed, {"check_thing_detected": {"status": "detected", "increase_pct": 0.42}})
    assert any("disallowed term" in p for p in problems)


def test_validate_rejects_unmatched_numbers():
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts="Balance increased by 999% to 9,999,999",  # no match to tool evidence
        hypothesis="test",
        suggested_action=agent.allowed_actions[0],
        caveats="test",
    )
    problems = agent._validate(parsed, {"check_thing_detected": {"status": "detected", "increase_pct": 0.42}})
    assert any("no number" in p for p in problems)


def test_validate_accepts_a_matching_number():
    assert _numeric_tolerance_ok(
        "The balance is now approximately 12345.0 EUR",
        {"t": {"status": "detected", "current_balance": 12345.0}},
    )


def test_validate_rejects_action_when_nothing_detected():
    agent = DomainAgent(domain="deposits", tools=[check_thing_not_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts="No signals found.",
        hypothesis="test",
        suggested_action=agent.allowed_actions[0],  # not "monitor only"
        caveats="test",
    )
    problems = agent._validate(parsed, {"check_thing_not_detected": {"status": "not_detected"}})
    assert any("monitor only" in p for p in problems)


# --- Opt-in live test: real local Ollama, real Strands Agent ---------------

def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_agent_produces_a_validated_or_fallback_result():
    """Not asserting the LLM's narrative quality -- only that the whole
    real pipeline (Strands Agent + the profile's real local model + tool calling +
    validate-or-fallback) runs to completion and returns a well-formed
    result either way, exactly like ollama_narrator.py's existing
    live-dependent tests are scoped."""
    model = get_model(ModelConfig(mode="local"))
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=model)
    result = agent.evaluate("PRTY00001")
    assert result.suggested_action in agent.allowed_actions
    assert result.narrative_source  # either "strands+ollama:..." or a labeled fallback


# --- Regressions found in a live run ---------------------------------------

def test_percentage_phrasing_of_a_fractional_value_passes_validation():
    """A live run rejected a correct narrative ('76.83% increase') because
    evidence stores change_pct as 0.7683. The RM then got the template
    instead of the better narrative."""
    evidence = {"t": {"status": "detected", "change_pct": 0.7683}}
    assert _numeric_tolerance_ok("Incoming payments rose 76.83% this quarter", evidence)
    assert _numeric_tolerance_ok("Incoming payments rose 76.8% this quarter", evidence)


def test_percentage_scaling_does_not_make_unrelated_numbers_match():
    evidence = {"t": {"status": "detected", "change_pct": 0.7683}}
    assert not _numeric_tolerance_ok("Incoming payments rose 12% this quarter", evidence)


def test_fallback_is_readable_sentences_not_a_dict_dump():
    """What an RM reads whenever the LLM fails validation must be prose."""
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    facts = agent.evaluate("PRTY00001").observed_facts
    assert "{" not in facts and "'status'" not in facts
    assert "42" in facts  # the real figure survives into the sentence


def test_describe_evidence_known_and_unknown_tools():
    from agents.domain_agent import describe_evidence

    known = describe_evidence("check_revenue_pattern_change", {
        "status": "detected", "change_pct": 0.7683, "prior_mean_amount": 4871.8,
        "recent_mean_amount": 8614.74, "event_date": "2025-08-21"})
    assert "76.8%" in known and "EUR 4,872" in known and "{" not in known

    unknown = describe_evidence("check_some_future_domain_signal",
                                 {"status": "detected", "score": 3, "evidence_ref": "X"})
    assert "{" not in unknown and "score 3" in unknown and "evidence" not in unknown


def test_score_fields_are_not_rescaled_as_percentages():
    """Regression: '76.83% exposure' validated against exposure_magnitude=
    0.7683 -- a 0..1 score presented as a percentage."""
    evidence = {"t": {"status": "detected", "exposure_magnitude": 0.7683}}
    assert not _numeric_tolerance_ok("The client has 76.83% exposure to the event", evidence)


def test_validate_rejects_foreign_currency_symbol():
    """Regression: a live narrative relabelled EUR amounts as '$'."""
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts="Balance increased 42% to $12,345.0",
        hypothesis="Client may be accumulating cash.",
        suggested_action=agent.allowed_actions[0], caveats="test",
    )
    evidence = {"check_thing_detected": {"status": "detected", "increase_pct": 0.42, "current_balance": 12345.0}}
    assert any("currency mismatch" in p for p in agent._validate(parsed, evidence))


def test_validate_accepts_eur_amounts():
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts="Balance increased 42% to EUR 12,345.0",
        hypothesis="Client may be accumulating cash.",
        suggested_action=agent.allowed_actions[0], caveats="test",
    )
    evidence = {"check_thing_detected": {"status": "detected", "increase_pct": 0.42, "current_balance": 12345.0}}
    assert agent._validate(parsed, evidence) == []


def test_validate_rejects_direction_contradiction():
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts="Incoming payments fell 76.83% this quarter",
        hypothesis="Client revenue is shrinking.",
        suggested_action=agent.allowed_actions[0], caveats="test",
    )
    evidence = {"t": {"status": "detected", "change_pct": 0.7683}}
    assert any("direction contradiction" in p for p in agent._validate(parsed, evidence))


def test_direction_check_ignores_level_fields():
    """'fell' is the correct word for a collateral coverage drop -- level
    fields like current_coverage_pct must not trigger the check."""
    from agents.domain_agent import _direction_problem

    evidence = {"t": {"status": "detected", "current_coverage_pct": 0.7, "prior_max_coverage_pct": 1.5}}
    assert _direction_problem("Collateral cover fell to 70.0%", evidence) is None


def test_model_label_is_auditable_not_an_object_repr():
    """Regression: narrative_source recorded '<OllamaModel object at 0x...>'
    -- useless as an audit trail entry."""
    from agents.domain_agent import model_label

    from agents.model_factory import default_model_id

    label = model_label(get_model(ModelConfig(mode="local")))
    assert label == default_model_id()  # R20: the profile is the only source of the id
    assert "object at" not in model_label(FailingModel())
