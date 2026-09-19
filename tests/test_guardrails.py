"""
Phase 4 guardrail tests (docs/generalization_plan.md) -- consolidates two
things every individual agent test file already partially covers into
one explicit, cross-cutting sweep:

  1. Every LLM-facing agent's validate function rejects EVERY term in
     BANNED_TERMS (agents/domain_agent.py), not just the one example
     term each agent's own test file happens to check. All four agents
     share the SAME tuple (imported, not copy-pasted) -- this is the
     test that would catch a new term added to it not actually being
     enforced everywhere, or a copy-paste drift introducing a second,
     out-of-sync list.
  2. No path under protected_evaluator_only/ (ground-truth labels) is
     ever constructible, and the literal strings that name it/its
     contents never appear in any file that could plausibly read data
     at runtime -- only in the generator that writes it, the dedicated
     evaluator that's allowed to read it, and the refusal checks
     themselves. A monkeypatched `open()` sweep, the mechanism
     originally sketched in the plan, would give false confidence here:
     OfflineLocalSource/FdmLocalSource read CSVs through DuckDB's own
     C-level file I/O (`read_csv_auto('...')`), which never calls
     Python's `open()` at all -- so this is a static source sweep
     instead, which actually catches what matters.
"""

from __future__ import annotations

import os

import pytest

from agents.domain_agent import BANNED_TERMS, DomainAgent, DomainNarrative
from agents.investigator_agent import InvestigationFields
from agents.investigator_agent import _validate as _investigator_validate
from agents.rm_copilot_agent import _validate as _copilot_validate
from datainsights.domain_registry import category_evidence_requirements, category_options
from datainsights.sources.base import DataSourceError
from datainsights.sources.offline_local import OfflineLocalSource
from external_events.event_extraction_agent import _build_fields_model
from external_events.event_extraction_agent import _validate as _extraction_validate
from tests._stub_model import FailingModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check_thing_detected(prty_id: str) -> dict:
    return {"status": "detected", "increase_pct": 0.42}


# --- 1. every agent rejects every banned term, not just one example -------

@pytest.mark.parametrize("term", BANNED_TERMS)
def test_domain_agent_rejects_every_banned_term(term):
    agent = DomainAgent(domain="deposits", tools=[check_thing_detected], model=FailingModel())
    parsed = DomainNarrative(
        observed_facts=f"A 42% increase was observed, related to {term} concerns.",
        hypothesis="Client activity increased.", suggested_action="No action -- monitor only",
        caveats="none",
    )
    problems = agent._validate(parsed, {"check_thing_detected": {"status": "detected", "increase_pct": 0.42}})
    assert any("disallowed term" in p for p in problems), f"term {term!r} was not rejected: {problems}"


@pytest.mark.parametrize("term", BANNED_TERMS)
def test_rm_copilot_rejects_every_banned_term(term):
    row = {"prty_id": "PRTY00001", "indicative_revenue_eur": 1000.0}
    problems = _copilot_validate(f"This relates to {term} matters.", row)
    assert any("disallowed term" in p for p in problems), f"term {term!r} was not rejected: {problems}"


@pytest.mark.parametrize("term", BANNED_TERMS)
def test_investigator_rejects_every_banned_term(term):
    options = category_options("fixed_rate_expiry")
    requirements = category_evidence_requirements("fixed_rate_expiry")
    parsed = InvestigationFields(
        proposed_category="TREASURY_OPPORTUNITY",
        reasoning=f"Balance is rising, unrelated to {term}.",
        evidence_refs=["x"], could_not_determine="",
    )
    tool_results = {"get_recent_balance_trend": {"status": "ok", "direction": "rising"}}
    problems = _investigator_validate(parsed, options, requirements, tool_results)
    assert any("disallowed term" in p for p in problems), f"term {term!r} was not rejected: {problems}"


@pytest.mark.parametrize("term", BANNED_TERMS)
def test_event_extraction_rejects_every_banned_term(term):
    source_text = f"A public infrastructure tender worth EUR 3.2 million was awarded, {term} noted."
    fields_model = _build_fields_model("public_tender_award")
    raw = fields_model(
        event_type="public_tender_award", event_date="2026-06-14", affected_country="ES",
        affected_sector="C", severity=4, estimated_value_eur=3_200_000.0,
        quote=source_text, confidence=0.9,
    )
    from datetime import date
    problems = _extraction_validate(raw, source_text, date(2026, 6, 20), "public_tender_award")
    assert any("disallowed term" in p for p in problems), f"term {term!r} was not rejected: {problems}"


# --- 2. protected ground-truth labels: unconstructible + never referenced -

def test_offline_local_source_refuses_protected_root_path():
    with pytest.raises(DataSourceError, match="protected"):
        OfflineLocalSource(
            os.path.join(REPO_ROOT, "data_generator", "output", "protected_evaluator_only"),
            os.path.join(REPO_ROOT, "config", "entities.yaml"),
        )


# Files legitimately allowed to name protected_evaluator_only/trigger_events:
#   - the generator that WRITES the ground truth (never reads it back as input)
#   - the dedicated evaluator CLAUDE.md carves out as the one reader
#   - the refusal checks themselves (must name what they refuse)
#   - every other entry here is a docstring DISCLAIMING that the module
#     reads it -- kept in the allowlist rather than reworded, since
#     rewording a correct disclaimer isn't this test's job
_ALLOWED_REFERENCES = {
    os.path.join(REPO_ROOT, "data_generator", "generate_data.py"),
    os.path.join(REPO_ROOT, "evaluation", "evaluate.py"),
    os.path.join(REPO_ROOT, "datainsights", "sources", "offline_local.py"),
    os.path.join(REPO_ROOT, "datainsights", "sources", "fdm_local.py"),
    os.path.join(REPO_ROOT, "datainsights", "sources", "snowflake_source.py"),
    os.path.join(REPO_ROOT, "detection_engine", "large_incoming_payment.py"),
    os.path.join(REPO_ROOT, "datainsights", "rm_feedback.py"),
    os.path.join(REPO_ROOT, "datainsights", "ml", "baselines.py"),
    os.path.join(REPO_ROOT, "datainsights", "ml", "evaluate_baselines.py"),
    os.path.join(REPO_ROOT, "datainsights", "ml", "label_pipeline.py"),
    os.path.join(REPO_ROOT, "external_events", "sample_notices.py"),
}

# Directories a live pipeline run could actually execute code from --
# scoped, not the whole repo (excludes tests/, evaluation/, data_generator/,
# docs/, which legitimately discuss or produce the protected data).
_LIVE_CODE_DIRS = ["agents", "detection_engine", "datainsights", "external_events"]


def test_protected_labels_never_referenced_outside_the_allowed_set():
    hits = []
    for live_dir in _LIVE_CODE_DIRS:
        for root, _, files in os.walk(os.path.join(REPO_ROOT, live_dir)):
            if "__pycache__" in root:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                if path in _ALLOWED_REFERENCES:
                    continue
                with open(path) as f:
                    text = f.read()
                if "protected_evaluator_only" in text or "trigger_events" in text:
                    hits.append(path)
    assert hits == [], (
        f"live pipeline code references protected ground-truth labels outside the "
        f"allowed set: {hits}"
    )
