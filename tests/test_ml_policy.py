"""
T2 (docs/ml_strategy_plan.md §9) -- the ML policy loader and its
precedence rule: explicit user policy > Gate1+binding > LLM proposal > none.
"""

from __future__ import annotations

import pytest
import yaml

from datainsights.ml.policy import MlPolicyError, load_policy


def test_no_policy_file_returns_empty_policy_not_an_error(tmp_path):
    policy = load_policy("fdm", path=tmp_path / "does_not_exist.yaml")
    assert policy.measures == {}
    assert policy.defaults.algorithm == "deterministic"


def test_the_real_shipped_policy_enables_the_e2_measure():
    policy = load_policy("fdm")  # config/ml_policy.yaml, the real file
    resolved = policy.resolve("BalanceObservation.balance")
    assert resolved.enabled is True
    assert resolved.algorithm == "isolation_forest"
    assert resolved.chosen_by == "policy"


def test_explicit_policy_disables_even_when_structurally_eligible(tmp_path):
    path = tmp_path / "ml_policy.yaml"
    path.write_text(yaml.safe_dump({
        "schemas": {"fdm": {"measures": {
            "Transaction.amount": {"enabled": False, "chosen_by": "policy",
                                   "disabled_reason": "RM feedback says these are seasonal, not growth"}
        }}}
    }))
    policy = load_policy("fdm", path=path)
    resolved = policy.resolve("Transaction.amount", gate1_eligible=True, mapped_by_binding=True)
    assert resolved.enabled is False
    assert resolved.chosen_by == "policy"
    assert "seasonal" in resolved.reason


def test_gate1_and_binding_together_enable_with_no_config(tmp_path):
    policy = load_policy("fdm", path=tmp_path / "missing.yaml")
    resolved = policy.resolve("Transaction.amount", gate1_eligible=True, mapped_by_binding=True)
    assert resolved.enabled is True
    assert resolved.chosen_by == "gate1+binding"
    assert resolved.algorithm == "deterministic"  # the schema default, not silently ML


def test_gate1_alone_without_a_binding_mapping_does_not_enable(tmp_path):
    policy = load_policy("fdm", path=tmp_path / "missing.yaml")
    resolved = policy.resolve("SomeUnmappedField", gate1_eligible=True, mapped_by_binding=False)
    assert resolved.enabled is False
    assert resolved.chosen_by == "none"


def test_llm_proposed_measure_stays_disabled_until_a_human_accepts_it(tmp_path):
    policy = load_policy("fdm", path=tmp_path / "missing.yaml")
    resolved = policy.resolve("covenant_headroom_pct", llm_proposed=True)
    assert resolved.enabled is False
    assert resolved.chosen_by == "llm_proposal"
    assert "no human has accepted" in resolved.reason


def test_unknown_algorithm_raises_naming_the_valid_set(tmp_path):
    path = tmp_path / "ml_policy.yaml"
    path.write_text(yaml.safe_dump({
        "schemas": {"fdm": {"measures": {
            "X.y": {"enabled": True, "algorithm": "neural_net_v7"}
        }}}
    }))
    with pytest.raises(MlPolicyError, match="neural_net_v7"):
        load_policy("fdm", path=path)


def test_unexpected_field_in_policy_yaml_is_rejected_not_ignored(tmp_path):
    """extra='forbid' -- a typo'd field (e.g. 'enalbed') must fail loudly,
    not be silently dropped and leave the measure disabled by accident."""
    path = tmp_path / "ml_policy.yaml"
    path.write_text(yaml.safe_dump({
        "schemas": {"fdm": {"measures": {"X.y": {"enalbed": True}}}}
    }))
    with pytest.raises(MlPolicyError):
        load_policy("fdm", path=path)
