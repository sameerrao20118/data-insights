"""
T1 (docs/ml_strategy_plan.md §9) -- the ML-eligibility profiler.
Deterministic, no Ollama, no model fitting.
"""

from __future__ import annotations

import os

import pytest

from onboarding.ml_profiler import assess
from onboarding.profiler import profile_directory

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_KERNEL_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm", "kernel")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_KERNEL_DIR), reason="FDM data not generated")


@pytest.fixture(scope="module")
def results():
    profiles = profile_directory(FDM_KERNEL_DIR)
    return assess(FDM_KERNEL_DIR, profiles)


def test_the_real_e2_measure_is_eligible(results):
    """AGRMNT_LDGR_BAL_AMT (the column datainsights/ml/baselines.py's
    IsolationForestBaseline actually challenges) must come back eligible
    against the real shipped FDM data -- if this ever goes False, the E2
    slot has silently lost its only real measure."""
    cols = {c.column: c for c in results["agreement_daily_balance"]}
    assert cols["AGRMNT_LDGR_BAL_AMT"].eligible is True
    assert cols["AGRMNT_LDGR_BAL_AMT"].entity_column == "AGRMNT_ID"
    assert cols["AGRMNT_LDGR_BAL_AMT"].observations_per_entity >= 8


def test_sparse_history_is_rejected_with_a_specific_reason(results):
    """party.RSK_GRD_VAL has ~1 observation per party (a risk grade
    rarely changes) -- must be rejected on observation count, not
    silently marked eligible."""
    col = next(c for c in results["party"] if c.column == "RSK_GRD_VAL")
    assert col.eligible is False
    assert any("observation" in r for r in col.reasons)


def test_identifier_columns_never_appear_as_a_measure(results):
    """A numeric id column (event_financial.FIN_EVNT_TYP_ID) is real data
    here but must never be assessed as eligible -- it's a key, not a
    measure."""
    col = next(c for c in results["event_financial"] if c.column == "FIN_EVNT_TYP_ID")
    assert col.eligible is False


def test_high_null_rate_is_rejected(results):
    col = next(c for c in results["agreement"] if c.column == "AGRMNT_ORIG_LIM")
    assert col.eligible is False
    assert any("null rate" in r for r in col.reasons)


def test_non_numeric_columns_are_never_assessed(results):
    """A baseline models a magnitude -- a string/categorical column is
    out of scope by construction, never even a rejected entry."""
    assessed_cols = {c.column for table in results.values() for c in table}
    assert "NACE_SECTION_CD" not in assessed_cols  # party_demographic, a string sector code
    assert "PRTY_ID" not in assessed_cols          # an identifier string, not numeric


def test_custom_thresholds_change_the_verdict():
    """The gate is configurable, not hard-coded -- lowering the floor to
    what the real sparse data actually has must flip the verdict."""
    profiles = profile_directory(FDM_KERNEL_DIR)
    lenient = assess(FDM_KERNEL_DIR, profiles, min_observations=1, min_entities=1)
    col = next(c for c in lenient["party"] if c.column == "RSK_GRD_VAL")
    assert col.eligible is True
