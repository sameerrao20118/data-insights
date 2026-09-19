"""
Phase 5a schema onboarding -- live proof (docs/generalization_plan.md).
Runs the real proposer (local Ollama) against the real SBA data
(data_generator/output_fdm_sba/, itself a real schema this repo already
knows the hand-built "right answer" for -- config/bindings/sba.yaml) as
a genuine blind test, then runs the accepted proposal's binding through
the actual detector tools, same as tests/test_sba_binding_end_to_end.py
does for the hand-built one.

Not asserting the model gets every concept right -- a live run found it
over-reaches on RiskGradeVersion/PartyMetricVersion (proposing columns
that exist but don't carry the right semantic, e.g. legal_name as a
PartyMetricVersion.value), which is real, disclosed, and exactly why
human review is mandatory, not decorative. What IS asserted: the
concepts with an unambiguous real answer (BalanceObservation,
Transaction, both a straight column-name match in the real data) come
back correct and usable end to end, proving the mechanism -- not just
the plumbing -- actually works.
"""

from __future__ import annotations

import os

import pytest

from onboarding.accept import accept
from onboarding.binding_proposer import propose_binding
from onboarding.entity_contract_generator import contract_yaml_text
from onboarding.profiler import profile_directory
from onboarding.proposal_writer import write_proposal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBA_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm_sba")

pytestmark = pytest.mark.skipif(not os.path.isdir(SBA_DIR), reason="SBA data not generated")


def _ollama_reachable() -> bool:
    import urllib.request
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_onboarding_end_to_end_against_real_sba_data(tmp_path, monkeypatch):
    from agents.model_factory import ModelConfig, get_model

    schema_name = "sba_onboarding_live_test"
    proposals_dir = str(tmp_path / "proposals")
    monkeypatch.setattr("onboarding.proposal_writer.PROPOSALS_DIR", proposals_dir)
    monkeypatch.setattr("onboarding.accept.PROPOSALS_DIR", proposals_dir)

    model = get_model(ModelConfig(mode="local"))
    profiles = profile_directory(SBA_DIR)
    proposals = propose_binding(profiles, model)

    # The two concepts with an unambiguous real answer -- straight
    # column-name matches in the actual generated data -- must come
    # back usable. This is the genuine "does the mechanism work" proof,
    # not a claim every concept is proposed correctly.
    assert proposals["BalanceObservation"].entity == "balance"
    assert proposals["BalanceObservation"].field_mappings.get("balance") == "balance"
    assert proposals["Transaction"].entity == "transaction"
    assert proposals["Transaction"].field_mappings.get("account_id") == "account_id"

    contract_yaml = contract_yaml_text(profiles, schema_name)
    write_proposal(schema_name, f"config/entities_{schema_name}.yaml", proposals, contract_yaml)

    # Force the riskier concepts to unavailable before accepting -- the
    # human-review step this test stands in for. Doing this in-place on
    # the written YAML, not by re-running the proposer, mirrors what a
    # real reviewer would do: edit the proposed file, then accept it.
    proposal_path = os.path.join(proposals_dir, schema_name, "binding.proposed.yaml")
    import yaml
    with open(proposal_path) as f:
        raw = yaml.safe_load(f)
    for risky in ("RiskGradeVersion", "PartyMetricVersion"):
        raw["concepts"][risky] = {"unavailable": "held back for human review in this test"}
    with open(proposal_path, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False)

    binding_path, contract_path = accept(schema_name, repo_root=str(tmp_path))
    assert os.path.exists(binding_path)
    assert os.path.exists(contract_path)

    # Run it through the real pipeline: CanonicalSource + the same
    # detector tools every other schema this session proved uses.
    from datainsights.semantic.binding import load_binding
    from datainsights.semantic.canonical import CanonicalSource
    from datainsights.sources.offline_local import OfflineLocalSource

    binding = load_binding(os.path.join(str(tmp_path), "config", "bindings", f"{schema_name}.yaml"))
    canonical = CanonicalSource(OfflineLocalSource(SBA_DIR, os.path.join(str(tmp_path), "config", f"entities_{schema_name}.yaml")),
                                binding)
    balances = canonical.read("BalanceObservation")
    assert not balances.empty
    transactions = canonical.read("Transaction", account_id=balances.iloc[0]["account_id"])
    assert isinstance(transactions, type(balances))  # a real DataFrame back, not an error
