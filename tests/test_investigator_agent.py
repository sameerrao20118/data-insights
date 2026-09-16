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

from agents.investigator_agent import investigate, make_investigator_tools
from datainsights.correlation.hypothesis import Recommendation
from datainsights.sources.fdm_local import FdmLocalSource
from tests._stub_model import FailingModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


@pytest.fixture(scope="module")
def source():
    return FdmLocalSource(FDM_DIR, CONTRACT_PATH)


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

    model = get_model(ModelConfig(mode="local", model_id="qwen2.5:7b"))
    note = investigate(make_rec(), source, model, date(2025, 10, 4))

    assert note is not None
    print(f"\nInvestigation: proposed={note.proposed_category} status={note.status}")
    print(f"  reasoning: {note.reasoning}")
    print(f"  could_not_determine: {note.could_not_determine}")
    # The hard boundary, not a specific proposal: whatever it proposed,
    # it must be one of the two registered options -- never invented.
    assert note.proposed_category in ("TREASURY_OPPORTUNITY", "HEDGING_NEED")
