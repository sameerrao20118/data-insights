"""
M7 verification: the domain registry's "done when" bar from
docs/adding_a_new_domain.md -- a new domain registers its tools, allowed
actions, and per-signal category/hypothesis WITHOUT editing
agents/orchestrator.py, agents/domain_agent.py, or
datainsights/correlation/hypothesis.py. Two registries make up the whole
mechanism (see agents/domain_registry.py and
datainsights/domain_registry.py's docstrings for why they're split):
this file proves each half independently, plus the combined result an
orchestrator-shaped caller would see.
"""

from __future__ import annotations

import yaml

from agents import domain_registry as agent_registry
from agents.domain_agent import DomainAgent
from agents.domain_registry import DomainSpec
from agents.domain_registry import all_specs as agent_all_specs
from agents.domain_registry import detector_by_tool as agent_detector_by_tool
from agents.domain_registry import get as agent_get
from agents.domain_registry import register as agent_register
from datainsights import domain_registry as data_registry

import agents.tools  # noqa: F401,E402 -- registers the REAL domains; without this the test
#                       only passed when another test had imported it first (order-dependent)


def _dummy_make_tools(source, rules, as_of):
    return ["dummy-tool-for-source-" + str(source)]


class _DummyDetectorModule:
    """Stands in for a real detection_engine module -- only its identity
    (not its contents) matters to detector_by_tool()."""


def test_dummy_domain_registers_its_tools_without_editing_orchestrator():
    """The code half: agents/domain_registry.py. A new DomainSpec appears
    in all_specs()/detector_by_tool() the moment it's registered -- the
    exact mechanism agents/orchestrator.py already reads through (see
    its `import agents.tools` + `all_specs()` usage), with no edit to
    orchestrator.py itself required to pick this one up."""
    agent_register(DomainSpec(
        name="widgets",
        make_tools=_dummy_make_tools,
        detector_by_tool={"check_widget_spike": _DummyDetectorModule},
    ))
    try:
        assert "widgets" in agent_all_specs()
        assert agent_get("widgets").make_tools("SRC", {}, "2026-01-01") == ["dummy-tool-for-source-SRC"]
        assert agent_detector_by_tool()["check_widget_spike"] is _DummyDetectorModule
        # Real domains registered by agents/tools.py (imported transitively
        # via agents.domain_agent -> ... -- see below) are still present
        # alongside the dummy one; registering doesn't clobber them.
        assert "check_cash_buildup" in agent_detector_by_tool()
    finally:
        del agent_registry._REGISTRY["widgets"]  # tidy up for later tests


def test_dummy_domain_allowed_actions_and_signal_mapping_without_editing_hypothesis_or_domain_agent(
        tmp_path, monkeypatch):
    """The data half: datainsights/domain_registry.py, loaded from
    config/domains_fdm.yaml. A fixture file stands in for that config
    (never touching the real one) and proves both consumers --
    agents/domain_agent.py's DomainAgent and
    datainsights/correlation/hypothesis.py's category_for/hypothesis_for
    -- read through the SAME lookup with zero code change on their side."""
    real_config = data_registry._load()
    fixture = dict(real_config)
    fixture["widgets"] = {
        "allowed_actions": ["RM to review widget signals", "No action -- monitor only"],
        "signals": {"widget_spike": {"category": "ADVISORY_ONLY", "hypothesis": "Widgets spiked."}},
    }
    fixture_path = tmp_path / "domains_fdm.yaml"
    fixture_path.write_text(yaml.safe_dump(fixture))

    monkeypatch.setattr(data_registry, "_DEFAULT_PATH", str(fixture_path))
    data_registry._load.cache_clear()
    try:
        # DomainAgent (agents/domain_agent.py) -- zero edits to that file.
        agent = DomainAgent(domain="widgets", tools=[], model=None)
        assert agent.allowed_actions == ("RM to review widget signals", "No action -- monitor only")

        # hypothesis.py's category/hypothesis lookup -- zero edits to that
        # file either.
        assert data_registry.category_for("widget_spike") == "ADVISORY_ONLY"
        assert data_registry.hypothesis_for("widget_spike") == "Widgets spiked."

        # Real domains/signals from the original file are still there too
        # (the fixture extends, not replaces, the real config).
        assert data_registry.category_for("cash_buildup") == "TREASURY_OPPORTUNITY"
    finally:
        data_registry._load.cache_clear()  # restore the real config for every other test
