"""
Regression tests for two defects found reviewing M17 against its own
plan -- both cases of a control that LOOKED like it worked but didn't.

Defect 1: datainsights/ml/runner.py never checked `enabled`. It only
skipped when algorithm == "deterministic", so a measure with
enabled=false but algorithm=isolation_forest STILL RAN. The dashboard's
Enabled checkbox -- the primary user-facing control, and the thing
docs/ml_quickstart.md tells a user to rely on -- was a silent no-op in
that combination. A control that silently does nothing is worse than no
control, because the user believes they've acted.

Defect 2 is covered in tests/test_rm_entitlement.py's worklist test plus
the dashboard AppTest; this file owns the policy/runner half.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from datainsights.ml.runner import run_measure

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
AS_OF = date(2025, 10, 4)

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


@pytest.fixture(scope="module")
def runtime():
    from datainsights.runtime import build_runtime

    return build_runtime("fdm_local")


def test_a_disabled_measure_does_not_run_even_with_a_challenger_algorithm(runtime):
    """THE regression. enabled=False + algorithm=isolation_forest must
    skip -- before the fix this ran the full comparison anyway."""
    result = run_measure("BalanceObservation.balance", runtime.source, runtime.rules, AS_OF,
                         algorithm="isolation_forest", chosen_by="policy", enabled=False)
    assert result.ran is False
    assert "disabled in policy" in result.skip_reason
    assert result.n_agreements == 0  # nothing was evaluated at all


def test_an_enabled_measure_with_a_challenger_still_runs(runtime):
    """Control: the fix must not disable everything by accident."""
    result = run_measure("BalanceObservation.balance", runtime.source, runtime.rules, AS_OF,
                         algorithm="isolation_forest", chosen_by="policy", enabled=True)
    assert result.ran is True
    assert result.n_agreements > 0


def test_disabled_beats_algorithm_in_the_skip_reason(runtime):
    """When a measure is both disabled AND deterministic, the reason the
    user sees should be the one they acted on (disabled), not the
    incidental one -- otherwise the UI explains the wrong cause."""
    result = run_measure("BalanceObservation.balance", runtime.source, runtime.rules, AS_OF,
                         algorithm="deterministic", chosen_by="policy", enabled=False)
    assert result.ran is False
    assert "disabled in policy" in result.skip_reason


def test_run_honours_a_disabled_policy_end_to_end(tmp_path, monkeypatch):
    """The whole chain: a policy file that disables a measure must
    produce a manifest showing ran=False -- proving the YAML a user (or
    the dashboard's Save button) writes actually reaches the runner."""
    import yaml

    import datainsights.ml.policy as policy_mod
    from datainsights.ml.runner import run

    policy_file = tmp_path / "ml_policy.yaml"
    policy_file.write_text(yaml.safe_dump({
        "schemas": {"fdm": {"measures": {
            "BalanceObservation.balance": {
                "enabled": False, "algorithm": "isolation_forest", "chosen_by": "policy",
                "disabled_reason": "turned off for this test",
            }
        }}}
    }))
    monkeypatch.setattr(policy_mod, "DEFAULT_POLICY_PATH", policy_file)

    result = run("fdm_local", measure_filter="BalanceObservation.balance")
    measure = result["measures"][0]
    assert measure["ran"] is False, "a disabled measure ran anyway -- the policy file is being ignored"
    assert "disabled in policy" in measure["skip_reason"]


def test_saving_policy_from_the_dashboard_preserves_the_documented_reasoning(tmp_path, monkeypatch):
    """A Save used to round-trip the YAML and silently delete the
    `power_criteria` reasoning block -- load-bearing documentation, not
    decoration. Caught by tests/test_ml_gate.py after it happened for
    real; this pins the fix at the place that caused it."""
    import dashboard.common as common

    policy = tmp_path / "config" / "ml_policy.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text(
        "# R7 -- the power criteria, with their reasoning.\n"
        "# Two levels, because they are two different claims.\n"
        "power_criteria:\n  ml_challenger:\n    min_entities: 200\n"
        "schemas:\n  fdm:\n    measures:\n      BalanceObservation.balance:\n"
        "        enabled: true\n        algorithm: isolation_forest\n        chosen_by: policy\n"
        "        hyperparameters: {n_estimators: 20}\n"
    )
    monkeypatch.setattr(common, "ROOT", tmp_path)
    common.save_ml_policy("fdm", {"Transaction.amount": (False, "deterministic")})

    after = policy.read_text()
    assert "# R7 -- the power criteria, with their reasoning." in after
    assert "two different claims" in after
    import yaml

    loaded = yaml.safe_load(after)
    assert loaded["power_criteria"]["ml_challenger"]["min_entities"] == 200      # untouched
    assert loaded["schemas"]["fdm"]["measures"]["Transaction.amount"]["enabled"] is False  # the edit landed
    # a hand-set hyperparameter on another measure survives the save
    assert loaded["schemas"]["fdm"]["measures"]["BalanceObservation.balance"]["hyperparameters"] == {"n_estimators": 20}
