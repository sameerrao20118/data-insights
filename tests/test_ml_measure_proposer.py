"""
T3 (docs/ml_strategy_plan.md §9) -- the Gate 3 LLM measure proposer.
Deterministic tests use tests/_stub_model.py's FailingModel to prove the
validate-or-reject discipline without any network call; one live test
(skipped if Ollama isn't reachable) proves it actually works end to end,
mirroring onboarding's existing test_onboarding_live.py pattern.
"""

from __future__ import annotations

import os

import pytest

from onboarding.ml_measure_proposer import propose_ml_measures
from onboarding.ml_profiler import MeasureEligibility
from onboarding.profiler import ColumnProfile, TableProfile
from tests._stub_model import FailingModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_KERNEL_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm", "kernel")


def _fake_profile() -> TableProfile:
    return TableProfile(
        physical_table="widget_ledger", row_count=500,
        columns=[
            ColumnProfile(name="WIDGET_ID", inferred_type="string", nullable=False, is_unique=False),
            ColumnProfile(name="HEADROOM_PCT", inferred_type="float", nullable=False, is_unique=False,
                         sample_values=["12.5", "8.1", "30.0"]),
        ],
        key_candidates=[], date_columns=["OBS_DT"], bitemporal_pair=None,
    )


def _fake_eligible() -> MeasureEligibility:
    return MeasureEligibility(table="widget_ledger", column="HEADROOM_PCT", eligible=True,
                              entity_column="WIDGET_ID", n_entities=50, observations_per_entity=15.0)


def test_a_failing_model_call_is_rejected_not_a_crash():
    """Same discipline as binding_proposer.py -- any exception during the
    LLM call becomes a rejected proposal, never propagates and never
    fabricates an accepted mapping."""
    eligibility = {"widget_ledger": [_fake_eligible()]}
    profiles = {"widget_ledger": _fake_profile()}
    proposals = propose_ml_measures(eligibility, profiles, FailingModel())
    assert len(proposals) == 1
    assert proposals[0].accepted is False
    assert "failed" in proposals[0].rejected_reason
    assert proposals[0].table == "widget_ledger"
    assert proposals[0].column == "HEADROOM_PCT"


def test_ineligible_columns_are_never_proposed():
    """propose_ml_measures must only ever call the model for candidates
    with eligible=True -- an ineligible column (e.g. failed Gate 1) must
    not produce ANY proposal, accepted or rejected."""
    ineligible = MeasureEligibility(table="widget_ledger", column="WIDGET_ID", eligible=False,
                                    reasons=["identifier"])
    eligibility = {"widget_ledger": [ineligible]}
    profiles = {"widget_ledger": _fake_profile()}
    proposals = propose_ml_measures(eligibility, profiles, FailingModel())
    assert proposals == []


def test_already_mapped_columns_are_skipped_never_re_proposed():
    """A column Gate 2 (the binding) already covers doesn't need a Gate 3
    proposal -- and must not even trigger a model call (FailingModel
    would surface as a rejected entry if it were called)."""
    eligibility = {"widget_ledger": [_fake_eligible()]}
    profiles = {"widget_ledger": _fake_profile()}
    proposals = propose_ml_measures(eligibility, profiles, FailingModel(),
                                    already_mapped={("widget_ledger", "HEADROOM_PCT")})
    assert proposals == []


def _ollama_reachable() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not os.path.isdir(FDM_KERNEL_DIR), reason="FDM data not generated")
@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_proposal_against_the_real_e2_measure_recognises_it_as_relevant():
    """Live, real Ollama call against the actual shipped column that
    datainsights/ml/baselines.py already challenges -- a sanity check
    that the model can tell a genuine measure apart from an identifier,
    not a claim every proposal it makes is correct."""
    from agents.model_factory import ModelConfig, get_model
    from onboarding.ml_profiler import assess
    from onboarding.profiler import profile_directory

    model = get_model(ModelConfig(mode="local"))
    profiles = profile_directory(FDM_KERNEL_DIR)
    eligibility = assess(FDM_KERNEL_DIR, profiles)

    proposals = propose_ml_measures(eligibility, profiles, model)
    balance = next(p for p in proposals
                  if p.table == "agreement_daily_balance" and p.column == "AGRMNT_LDGR_BAL_AMT")
    assert balance.accepted is True, balance.rejected_reason
    assert balance.confidence > 0
    assert balance.proposed_name
