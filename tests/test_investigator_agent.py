"""
A2 verification for agents/investigator_agent.py.

Deterministic tests use a hand-built Recommendation (ambiguous=True,
endogenous_signal_type="fixed_rate_expiry") against the REAL generated
FDM dataset -- no client in this 60-person book currently has an active
fixed_rate_expiry detection (checked directly), so this deliberately
exercises the investigator's read-only tools against a real client's
OTHER data, which is exactly what those tools are scoped to: general
client context, not detector-specific evidence.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from agents.investigator_agent import (
    EVIDENCE_PREDICATES,
    InvestigationFields,
    _validate,
    investigate,
    make_investigator_tools,
)
from datainsights.correlation.hypothesis import Recommendation
from datainsights.domain_registry import category_evidence_requirements, category_options
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.fdm_local import FdmLocalSource
from tests._stub_model import FailingModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


@pytest.fixture(scope="module")
def source():
    """Named `source` for minimal test-file churn; returns a
    CanonicalSource -- investigator tools read through it now
    (docs/generalization_plan.md Phase 1)."""
    return CanonicalSource(FdmLocalSource(FDM_DIR, CONTRACT_PATH), load_binding("fdm"))


def make_rec(**overrides):
    defaults = dict(
        prty_id="PRTY00001", nba_category="TREASURY_OPPORTUNITY", crm_text="x",
        hypothesis="A fixed-rate period ending soon...", recommended_action="x",
        business_value_score=200, confirming_domains=("lending",), signal_strength=2,
        endogenous_signal_type="fixed_rate_expiry", exogenous_event_type=None,
        exogenous_event_source=None, exogenous_event_date=None,
        evidence_ref="MORTGAGE_AGREEMENT:AGR000001:eff=2026-01-01", sizing_basis="x",
        ambiguous=True,
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


# --- tools work against real client data ----------------------------------

def test_get_client_context_returns_real_data(source):
    tools = make_investigator_tools(source, "PRTY00001", date(2025, 10, 4))
    ctx_tool = next(t for t in tools if t.tool_name == "get_client_context")
    result = ctx_tool()
    assert result["status"] != "not_found" if "status" in result else True
    assert "segment" in result


def test_get_currency_exposure_reports_eur_only(source):
    """This build's generator writes EUR throughout -- an honest run
    should report no non-EUR currencies, not fabricate exposure."""
    tools = make_investigator_tools(source, "PRTY00001", date(2025, 10, 4))
    fx_tool = next(t for t in tools if t.tool_name == "get_currency_exposure")
    result = fx_tool()
    assert result["non_eur_currencies"] == []


def test_tools_are_scoped_to_one_client_only(source):
    """Structural proof: the tools closed over PRTY00001 have no
    parameter that could reach PRTY00002's data -- calling them takes no
    prty_id argument at all."""
    tools = make_investigator_tools(source, "PRTY00001", date(2025, 10, 4))
    for t in tools:
        # Strands @tool-wrapped functions expose their signature; none of
        # these should accept a client-identifying argument.
        import inspect
        sig = inspect.signature(t._tool_func) if hasattr(t, "_tool_func") else None
        if sig:
            assert "prty_id" not in sig.parameters


# --- A2 consistency fix (docs/generalization_plan.md Phase 4) ------------
# A live run once proposed HEDGING_NEED while its own reasoning said "no
# multi-currency activity found" -- backwards. These tests prove the fix
# deterministically, no Ollama needed: _validate() recomputes evidence in
# Python and rejects a proposal whose predicate doesn't hold, regardless
# of how plausible the model's prose sounds.

def _fields(**overrides):
    defaults = dict(proposed_category="HEDGING_NEED", reasoning="Some reasoning.",
                    evidence_refs=["x"], could_not_determine="")
    defaults.update(overrides)
    return InvestigationFields(**defaults)


def test_hedging_need_rejected_without_non_eur_activity():
    """The exact backwards case a live run produced: HEDGING_NEED
    proposed, but this client's own tool results show no non-EUR
    currency activity at all."""
    tool_results = {"get_currency_exposure": {"currencies_seen": ["EUR"], "non_eur_currencies": []},
                    "get_recent_balance_trend": {"status": "ok", "direction": "flat_or_falling"}}
    problems = _validate(_fields(proposed_category="HEDGING_NEED"),
                         category_options("fixed_rate_expiry"),
                         category_evidence_requirements("fixed_rate_expiry"), tool_results)
    assert any("requires evidence" in p and "non_eur_currency_activity" in p for p in problems)


def test_hedging_need_accepted_with_genuine_non_eur_activity():
    tool_results = {"get_currency_exposure": {"currencies_seen": ["EUR", "USD"], "non_eur_currencies": ["USD"]},
                    "get_recent_balance_trend": {"status": "ok", "direction": "flat_or_falling"}}
    problems = _validate(_fields(proposed_category="HEDGING_NEED"),
                         category_options("fixed_rate_expiry"),
                         category_evidence_requirements("fixed_rate_expiry"), tool_results)
    assert problems == []


def test_treasury_opportunity_rejected_without_rising_balance():
    tool_results = {"get_currency_exposure": {"currencies_seen": ["EUR"], "non_eur_currencies": []},
                    "get_recent_balance_trend": {"status": "ok", "direction": "flat_or_falling"}}
    problems = _validate(_fields(proposed_category="TREASURY_OPPORTUNITY"),
                         category_options("fixed_rate_expiry"),
                         category_evidence_requirements("fixed_rate_expiry"), tool_results)
    assert any("requires evidence" in p and "balance_rising" in p for p in problems)


def test_treasury_opportunity_accepted_with_rising_balance():
    tool_results = {"get_currency_exposure": {"currencies_seen": ["EUR"], "non_eur_currencies": []},
                    "get_recent_balance_trend": {"status": "ok", "direction": "rising"}}
    problems = _validate(_fields(proposed_category="TREASURY_OPPORTUNITY"),
                         category_options("fixed_rate_expiry"),
                         category_evidence_requirements("fixed_rate_expiry"), tool_results)
    assert problems == []


def test_category_not_in_options_still_rejected_first():
    problems = _validate(_fields(proposed_category="RISK_REVIEW"),
                         category_options("fixed_rate_expiry"),
                         category_evidence_requirements("fixed_rate_expiry"), {})
    assert any("not in" in p for p in problems)


def test_a_category_with_no_requires_evidence_entry_is_unconstrained():
    """Not every category_options entry has to declare a predicate --
    one with none is validated only on membership/banned-terms/reasoning,
    same as before this fix existed."""
    problems = _validate(_fields(proposed_category="TREASURY_OPPORTUNITY"), ["TREASURY_OPPORTUNITY"],
                         {}, {})  # no evidence_requirements at all
    assert problems == []


def test_evidence_predicates_registry_matches_domains_fdm_yaml():
    """Every predicate name config/domains_fdm.yaml references must
    actually be registered -- a typo here would silently disable the
    whole check (KeyError caught nowhere), not fail loudly."""
    for predicate_name in category_evidence_requirements("fixed_rate_expiry").values():
        assert predicate_name in EVIDENCE_PREDICATES


# --- fallback discipline (no Ollama needed) -------------------------------

def test_model_failure_returns_needs_review_note_never_raises(source):
    note = investigate(make_rec(), source, FailingModel(), date(2025, 10, 4))
    assert note is not None
    assert note.status == "needs_review"
    assert note.proposed_category == note.original_category  # never silently changed


def test_non_ambiguous_signal_type_returns_none(source):
    """No category_options registered for this signal_type -- nothing to
    investigate, must return None, not attempt one anyway."""
    rec = make_rec(endogenous_signal_type="cash_buildup", ambiguous=False)
    note = investigate(rec, source, FailingModel(), date(2025, 10, 4))
    assert note is None


# --- Opt-in live test: real local Ollama ----------------------------------

def _ollama_reachable() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_investigation_proposes_a_valid_category(source):
    from agents.model_factory import ModelConfig, get_model

    model = get_model(ModelConfig(mode="local"))
    note = investigate(make_rec(), source, model, date(2025, 10, 4))

    assert note is not None
    print(f"\nInvestigation: proposed={note.proposed_category} status={note.status}")
    print(f"  reasoning: {note.reasoning}")
    print(f"  could_not_determine: {note.could_not_determine}")
    # The hard boundary, not a specific proposal: whatever it proposed,
    # it must be one of the two registered options -- never invented.
    assert note.proposed_category in ("TREASURY_OPPORTUNITY", "HEDGING_NEED")
    # Not asserting a specific outcome -- but IF this ever degrades to
    # needs_review, it must be for the real reason the A2 fix exists
    # (docs/generalization_plan.md Phase 4): a live run repeatedly
    # proposed HEDGING_NEED for PRTY00001 despite this client's own
    # currency exposure showing no non-EUR activity at all, caught every
    # time by the evidence-consistency check, never by a coincidental
    # unrelated failure.
    if note.status == "needs_review":
        assert "requires evidence" in note.could_not_determine
