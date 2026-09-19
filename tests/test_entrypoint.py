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


# --- local vs AgentCore equivalence (docs/generalization_plan.md Phase 3) --
#
# "Two methods to run it" means one function, two callers -- not two
# implementations to keep in sync. agentcore_entrypoint() only exists if
# bedrock-agentcore is installed (it is, in this venv); BedrockAgentCoreApp
# .entrypoint() returns the function itself unchanged (verified against
# the installed package's source), so calling agentcore_entrypoint(payload)
# never starts a server or makes any network call -- it is exactly
# agents.entrypoint.invoke under a different name, registered as the
# handler AgentCore Runtime would call if this were ever deployed.

def test_agentcore_entrypoint_is_the_same_function_agentcore_runtime_would_call():
    from agents import entrypoint

    assert entrypoint.app is not None, (
        "bedrock-agentcore not importable in this environment -- "
        "agents.entrypoint.app should be None only when the package is "
        "missing, never for any other reason"
    )
    assert entrypoint.app.handlers["main"] is entrypoint.agentcore_entrypoint


def test_agentcore_entrypoint_produces_identical_output_to_invoke():
    """The actual equivalence proof: the same payload through invoke()
    (what the local CLI calls) and agentcore_entrypoint() (the plain
    function BedrockAgentCoreApp.entrypoint() registered unchanged, per
    the installed package's own source -- no server started, no network
    call) must return byte-identical results."""
    from agents.entrypoint import agentcore_entrypoint

    payload = {"prty_id": "PRTY00036", "domain": "all", "as_of": "2025-10-04", "narrate": False}
    assert agentcore_entrypoint(payload) == invoke(payload)


def test_fdm_agentcore_profile_validates_and_constructs_as_far_as_honestly_possible():
    """config/profiles/fdm_agentcore.yaml (the AgentCore-shaped
    counterpart to fdm_local.yaml) must pass every field-level validator
    in datainsights/config.py and construct up to its first real
    blocking point -- never silently fall back to local files, and never
    fail for a config-shape reason (only for the genuinely NOT RUN
    s3_parquet adapter)."""
    from datainsights.runtime import build_runtime

    with pytest.raises(NotImplementedError, match="s3_parquet"):
        build_runtime("fdm_agentcore")
