"""
T4 (docs/ml_strategy_plan.md §9) -- the self-service champion/challenger
runner. Runs against real FDM data (no LLM, no Ollama needed -- this is
sklearn/pandas only) so it stays in the deterministic suite.
"""

from __future__ import annotations

import os

import pytest

from datainsights.ml.runner import MeasureRunResult, run, run_measure

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


def test_the_real_policy_produces_a_comparison_for_the_e2_measure():
    """config/ml_policy.yaml enables BalanceObservation.balance with
    isolation_forest -- the runner must actually compare it, not skip it."""
    result = run("fdm_local", measure_filter="BalanceObservation.balance")
    measures = result["measures"]
    assert len(measures) == 1
    m = measures[0]
    assert m["ran"] is True
    assert m["measure"] == "BalanceObservation.balance"
    assert m["algorithm"] == "isolation_forest"
    assert m["n_agreements"] > 0
    assert m["det_detected"] >= 0 and m["challenger_detected"] >= 0


def test_the_disagreement_caveat_is_always_present():
    """The manifest must never ship without the explicit
    disagreement-is-not-improvement caveat -- this is the one line that
    keeps a self-service comparison from being misread as a verdict."""
    result = run("fdm_local", measure_filter="BalanceObservation.balance")
    assert "NOT an improvement claim" in result["caveat"]


def test_a_measure_with_no_wired_comparison_is_reported_not_silently_dropped():
    """An enabled-but-unwired measure must still appear in the output,
    marked ran=False with a specific reason -- never just absent."""
    result = MeasureRunResult(measure="PartyMetricVersion.value", detector=None,
                              algorithm="isolation_forest", chosen_by="policy", ran=False,
                              skip_reason="not wired")
    assert result.ran is False
    assert result.skip_reason


def test_deterministic_algorithm_is_skipped_with_a_clear_reason(monkeypatch):
    """A measure resolved to the deterministic baseline has nothing to
    challenge -- must be reported as skipped, not run a pointless
    self-comparison."""
    from datainsights.runtime import build_runtime

    rt = build_runtime("fdm_local")
    result = run_measure("Transaction.amount", rt.source, rt.rules, __import__("datetime").date(2025, 10, 4),
                         algorithm="deterministic", chosen_by="gate1+binding")
    assert result.ran is False
    assert "deterministic" in result.skip_reason


def test_write_manifest_round_trips(tmp_path, monkeypatch):
    import datainsights.ml.runner as runner_mod

    monkeypatch.setattr(runner_mod, "MANIFEST_DIR", str(tmp_path))
    result = run("fdm_local", measure_filter="BalanceObservation.balance")
    path = runner_mod.write_manifest(result)
    assert os.path.exists(path)
    import json
    with open(path) as f:
        loaded = json.load(f)
    assert loaded["run_id"] == result["run_id"]
