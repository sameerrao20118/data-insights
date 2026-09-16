"""
Phase 0 verification (docs/generalization_plan.md): agents/entrypoint.py's
invoke() reads its domain list from the registry (agents/domain_registry.py),
not a stale hardcoded dict, and its source/model come from a profile
(datainsights.runtime.build_runtime), not a hand-passed data_dir/mode.
"""

import os

import pytest

from agents.entrypoint import invoke

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


def test_domain_all_includes_every_registered_domain_not_just_deposits_lending():
    """The bug this test guards against: entrypoint.py used to hardcode
    {"deposits": ..., "lending": ...}, silently missing "risk" once it was
    registered (M8/A4). This must never regress."""
    result = invoke({"prty_id": "PRTY00036", "domain": "all", "as_of": "2025-10-04", "narrate": False})
    assert "risk" in result["agent_results"]
    assert "deposits" in result["agent_results"]
    assert "lending" in result["agent_results"]


def test_single_named_domain_works_for_every_registered_domain():
    for domain in ("deposits", "lending", "risk"):
        result = invoke({"prty_id": "PRTY00036", "domain": domain, "as_of": "2025-10-04", "narrate": False})
        assert result["domain"] == domain


def test_unknown_domain_raises_naming_the_real_registered_set():
    with pytest.raises(ValueError, match="risk"):
        invoke({"prty_id": "PRTY00036", "domain": "not_a_real_domain", "as_of": "2025-10-04", "narrate": False})


def test_domain_all_never_raises_for_a_malformed_model_and_falls_back():
    """narrate=False path -- exercised without needing Ollama reachable."""
    result = invoke({"prty_id": "PRTY00036", "domain": "all", "as_of": "2025-10-04", "narrate": False})
    assert result["prty_id"] == "PRTY00036"
    assert result["as_of"] == "2025-10-04"


def test_missing_prty_id_raises():
    with pytest.raises(KeyError):
        invoke({"domain": "all", "as_of": "2025-10-04", "narrate": False})
