"""
R6: a binding's `rules:` block is merged over config/rules.yaml per schema.
R8: propose emits the profile; accept validates and writes it, so
accept -> run needs no hand edits.
"""

from __future__ import annotations

import os

import pytest
import yaml

from datainsights.config import Profile
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding, merge_rules
from onboarding import accept as accept_mod
from onboarding.proposal_writer import profile_yaml_text, write_proposal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_merge_is_per_leaf_and_mutates_neither_input():
    base = {"cash_buildup": {"window_days": 60, "min_prior_balance": 5000}, "dormancy": {"dormancy_days": 60}}
    over = {"cash_buildup": {"min_prior_balance": 2500}}
    out = merge_rules(base, over)
    assert out == {"cash_buildup": {"window_days": 60, "min_prior_balance": 2500}, "dormancy": {"dormancy_days": 60}}
    assert base["cash_buildup"]["min_prior_balance"] == 5000 and over == {"cash_buildup": {"min_prior_balance": 2500}}


@pytest.mark.skipif(not os.path.isdir(os.path.join(REPO_ROOT, "data_generator", "output_fdm_sba")),
                    reason="SBA data not generated")
def test_sba_sets_its_own_floor_without_touching_fdm():
    assert load_binding("sba").rules["cash_buildup"]["min_prior_balance"] == 2500
    assert load_binding("fdm").rules == {}
    sba = build_runtime("sba_local", cache=False).rules
    fdm = build_runtime("fdm_local", cache=False).rules
    assert sba["cash_buildup"]["min_prior_balance"] == 2500
    assert fdm["cash_buildup"]["min_prior_balance"] == 5000
    assert sba["cash_buildup"]["window_days"] == fdm["cash_buildup"]["window_days"]  # untouched leaves inherited
    with open(os.path.join(REPO_ROOT, "config", "rules.yaml")) as f:
        assert yaml.safe_load(f)["cash_buildup"]["min_prior_balance"] == 5000


def test_proposed_profile_is_a_valid_profile_with_one_model_source():
    text = profile_yaml_text("acme", "data_generator/output_acme", "config/entities_acme.yaml")
    prof = Profile.model_validate(yaml.safe_load(text))
    assert prof.profile == "acme_local" and prof.source.binding == "acme"
    assert prof.source.cost_policy == "no_cost_local_files"
    from agents.model_factory import default_model_id
    assert prof.llm.model == default_model_id()


def test_accept_writes_binding_contract_and_profile_into_a_clean_repo_root(tmp_path, monkeypatch):
    name = "acmetest"
    proposals_dir = tmp_path / "proposals"
    monkeypatch.setattr(accept_mod, "PROPOSALS_DIR", str(proposals_dir))
    import onboarding.proposal_writer as pw
    monkeypatch.setattr(pw, "PROPOSALS_DIR", str(proposals_dir))
    contract = {"entities": {"parties": {"physical_table": "parties",
                                          "required_columns": {"party_id": {"type": "string"}}}}}
    from onboarding.binding_proposer import ConceptProposal
    proposals = {"Party": ConceptProposal(concept="Party", entity="parties", field_mappings={"party_id": "party_id"},
                                          confidence=0.9, evidence="", unavailable_reason=None)}
    write_proposal(name, f"config/entities_{name}.yaml", proposals, yaml.safe_dump(contract),
                   profile_yaml=profile_yaml_text(name, "data_generator/output_acme", f"config/entities_{name}.yaml"))
    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    b, c = accept_mod.accept(name, repo_root=str(repo))
    assert os.path.exists(b) and os.path.exists(c)
    profile_path = repo / "config" / "profiles" / f"{name}_local.yaml"
    assert profile_path.exists()
    with open(profile_path) as f:
        Profile.model_validate(yaml.safe_load(f))
    with pytest.raises(accept_mod.AcceptError, match="already exists"):
        accept_mod.accept(name, repo_root=str(repo))
