"""
R7 (docs/refactor_plan.md; docs/hardcoding_audit.md §3) -- the ML gate is
principled and stated in config, and on today's data it says NO ML
challenger is eligible, naming the criterion that failed.
"""

from __future__ import annotations

import os

import pytest
import yaml

from datainsights.ml.policy import PowerCriteria, load_power_criteria, outcome_label_count
from onboarding.ml_profiler import assess, ml_challenger_verdict
from onboarding.profiler import profile_directory

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_KERNEL = os.path.join(REPO_ROOT, "data_generator", "output_fdm", "kernel")
POLICY = os.path.join(REPO_ROOT, "config", "ml_policy.yaml")


def test_power_criteria_come_from_config_with_stated_reasoning():
    with open(POLICY) as f:
        raw = yaml.safe_load(f)
    assert "power_criteria" in raw, "the thresholds must be config, not code"
    pc = load_power_criteria()
    assert pc.ml_challenger.min_entities > pc.robust_baseline.min_entities
    assert pc.ml_challenger.min_outcome_labels > 0
    with open(POLICY) as f:
        text = f.read()
    assert "outcome labels" in text and "population" in text, "the reasoning must be in the file"


@pytest.mark.skipif(not os.path.isdir(FDM_KERNEL), reason="FDM data not generated")
def test_on_todays_fdm_data_no_ml_challenger_is_eligible_and_the_criterion_is_named():
    profiles = profile_directory(FDM_KERNEL)
    eligibility = assess(FDM_KERNEL, profiles)
    balance = next(c for c in eligibility["agreement_daily_balance"] if c.column == "AGRMNT_LDGR_BAL_AMT")
    assert balance.eligible, balance.reasons                     # robust-baseline comparison: fine
    assert not balance.ml_challenger_eligible                    # ML challenger: not on 92 accounts
    assert any("min_entities" in r for r in balance.ml_reasons)
    ok, why = ml_challenger_verdict(eligibility)
    assert ok is False and why and all(":" in w for w in why)


@pytest.mark.skipif(not os.path.isdir(FDM_KERNEL), reason="FDM data not generated")
def test_verdict_flips_when_the_data_would_actually_support_it():
    """The gate is a function of the criteria and the data, not a
    hardcoded 'no': loosen the criteria to what this dataset has plus a
    label store, and the same column becomes eligible."""
    profiles = profile_directory(FDM_KERNEL)
    loose = PowerCriteria.model_validate({
        "robust_baseline": {"min_observations_per_entity": 8, "min_entities": 30},
        "ml_challenger": {"min_entities": 50, "window_points": 30, "min_history_multiple_of_window": 1,
                          "min_outcome_labels": 0},
    })
    eligibility = assess(FDM_KERNEL, profiles, criteria=loose, outcome_labels=0)
    assert ml_challenger_verdict(eligibility)[0] is True


def test_outcome_label_count_is_zero_without_a_store(tmp_path):
    assert outcome_label_count(tmp_path / "nope.db") == 0
