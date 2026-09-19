"""
Phase 5a schema onboarding verification (docs/generalization_plan.md).
Deterministic tests (profiler, entity contract generation, proposal
rendering, the accept() validation gate) -- no Ollama needed, these are
the parts that must be correct regardless of what any model proposes.
The live end-to-end proof (a real LLM proposal against real data) is
tests/test_onboarding_live.py.

Guiding property tested throughout: onboarding is purely additive. No
test here ever writes to config/bindings/ or config/entities_*.yaml
under a name any existing profile uses -- accept() itself refuses to
overwrite an existing file without force=True, checked directly below.
"""

from __future__ import annotations

import os

import pytest
import yaml

from onboarding.accept import AcceptError, accept
from onboarding.binding_proposer import ConceptProposal
from onboarding.entity_contract_generator import generate_contract
from onboarding.profiler import ColumnProfile, TableProfile, profile_directory
from onboarding.proposal_writer import binding_yaml_text, review_report, write_proposal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_KERNEL_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm", "kernel")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_KERNEL_DIR), reason="FDM data not generated")


# --- profiler ----------------------------------------------------------

def test_profile_directory_finds_every_csv():
    profiles = profile_directory(FDM_KERNEL_DIR)
    assert "party" in profiles
    assert "agreement" in profiles
    assert all(isinstance(p, TableProfile) for p in profiles.values())


def test_profile_detects_real_bitemporal_pair():
    profiles = profile_directory(FDM_KERNEL_DIR)
    assert profiles["agreement"].bitemporal_pair == ("EFFECTIVE_START_DT", "EFFECTIVE_END_DT")


def test_profile_detects_key_candidates():
    profiles = profile_directory(FDM_KERNEL_DIR)
    assert "AGRMNT_ID" in profiles["agreement"].key_candidates


def test_profile_sample_values_are_truncated():
    profiles = profile_directory(FDM_KERNEL_DIR)
    for col in profiles["party"].columns:
        for v in col.sample_values:
            assert len(v) <= 41  # SAMPLE_TRUNCATE_CHARS + ellipsis


def test_profile_directory_ignores_non_csv_files(tmp_path):
    (tmp_path / "notes.txt").write_text("not a csv")
    assert profile_directory(str(tmp_path)) == {}


# --- entity contract generator ------------------------------------------

def _fake_profile(name: str) -> TableProfile:
    return TableProfile(
        physical_table=name, row_count=10,
        columns=[ColumnProfile("id", "string", nullable=False, is_unique=True),
                ColumnProfile("amount", "float", nullable=True, is_unique=False)],
        key_candidates=["id"], date_columns=[], bitemporal_pair=None,
    )


def test_generated_contract_has_provenance_marker():
    contract = generate_contract({"t1": _fake_profile("t1")}, "test_schema")
    assert contract["provenance"] == "profiled_from_user_schema"
    assert contract["entities"]["t1"]["primary_key"] == ["id"]
    assert "id" in contract["entities"]["t1"]["required_columns"]
    assert "amount" in contract["entities"]["t1"]["required_columns"]


# --- proposal writer -----------------------------------------------------

def _fake_proposals() -> dict[str, ConceptProposal]:
    return {
        "Party": ConceptProposal(concept="Party", entity="clients", field_mappings={"party_id": "client_id"},
                                 confidence=0.9, evidence="client_id looks like a party key", unavailable_reason=None),
        "CollateralValuation": ConceptProposal(concept="CollateralValuation", entity=None, field_mappings={},
                                               confidence=0.0, evidence="", unavailable_reason="no collateral table found"),
    }


def test_binding_yaml_text_shapes_a_real_loadable_binding(tmp_path):
    from datainsights.semantic.binding import Binding

    text = binding_yaml_text("test_schema", "config/entities_test_schema.yaml", _fake_proposals())
    raw = yaml.safe_load(text)
    binding = Binding.model_validate(raw)  # must not raise -- shape must be genuinely loadable
    assert binding.concepts["Party"].fields == {"party_id": "client_id"}
    assert binding.concepts["CollateralValuation"].unavailable == "no collateral table found"


def test_review_report_surfaces_confidence_and_unavailability():
    report = review_report("test_schema", _fake_proposals())
    assert "0.90" in report
    assert "UNAVAILABLE" in report
    assert "no collateral table found" in report


def test_write_proposal_writes_three_files(tmp_path, monkeypatch):
    monkeypatch.setattr("onboarding.proposal_writer.PROPOSALS_DIR", str(tmp_path))
    out_dir = write_proposal("test_schema", "config/entities_test_schema.yaml", _fake_proposals(),
                             "contract_version: 1\nentities: {}\n")
    assert os.path.exists(os.path.join(out_dir, "binding.proposed.yaml"))
    assert os.path.exists(os.path.join(out_dir, "entities.proposed.yaml"))
    assert os.path.exists(os.path.join(out_dir, "review.md"))


# --- accept() gate ---------------------------------------------------------

def _write_fake_proposal(proposals_dir: str, name: str, *, entity: str = "clients",
                         field_mappings: dict | None = None) -> None:
    os.makedirs(os.path.join(proposals_dir, name), exist_ok=True)
    binding = {"schema": name, "contract_ref": f"config/entities_{name}.yaml",
              "concepts": {"Party": {"entity": entity, "fields": field_mappings or {"party_id": "client_id"}}}}
    with open(os.path.join(proposals_dir, name, "binding.proposed.yaml"), "w") as f:
        yaml.safe_dump(binding, f)
    contract = {"contract_version": 1, "entities": {
        "clients": {"physical_table": "clients", "required_columns": {"client_id": {"type": "string"}}}}}
    with open(os.path.join(proposals_dir, name, "entities.proposed.yaml"), "w") as f:
        yaml.safe_dump(contract, f)


def test_accept_refuses_a_binding_with_no_matching_proposal(tmp_path):
    with pytest.raises(AcceptError, match="no proposal found"):
        accept("does_not_exist", repo_root=str(tmp_path))


def test_accept_writes_config_files_on_a_valid_proposal(tmp_path, monkeypatch):
    proposals_dir = str(tmp_path / "onboarding" / "proposals")
    monkeypatch.setattr("onboarding.accept.PROPOSALS_DIR", proposals_dir)
    _write_fake_proposal(proposals_dir, "test_schema")

    binding_path, contract_path = accept("test_schema", repo_root=str(tmp_path))
    assert os.path.exists(binding_path)
    assert os.path.exists(contract_path)
    with open(binding_path) as f:
        assert "clients" in f.read()


def test_accept_rejects_an_invalid_proposal_and_writes_nothing(tmp_path, monkeypatch):
    proposals_dir = str(tmp_path / "onboarding" / "proposals")
    monkeypatch.setattr("onboarding.accept.PROPOSALS_DIR", proposals_dir)
    # references a column that doesn't exist in the generated contract
    _write_fake_proposal(proposals_dir, "bad_schema", field_mappings={"party_id": "not_a_real_column"})

    with pytest.raises(AcceptError, match="failed validation"):
        accept("bad_schema", repo_root=str(tmp_path))
    assert not os.path.exists(os.path.join(tmp_path, "config", "bindings", "bad_schema.yaml"))


def test_accept_refuses_to_silently_overwrite_an_existing_binding(tmp_path, monkeypatch):
    proposals_dir = str(tmp_path / "onboarding" / "proposals")
    monkeypatch.setattr("onboarding.accept.PROPOSALS_DIR", proposals_dir)
    _write_fake_proposal(proposals_dir, "test_schema")
    accept("test_schema", repo_root=str(tmp_path))  # first accept succeeds

    with pytest.raises(AcceptError, match="already exists"):
        accept("test_schema", repo_root=str(tmp_path))  # second, without force, must refuse


def test_accept_force_true_allows_a_deliberate_overwrite(tmp_path, monkeypatch):
    proposals_dir = str(tmp_path / "onboarding" / "proposals")
    monkeypatch.setattr("onboarding.accept.PROPOSALS_DIR", proposals_dir)
    _write_fake_proposal(proposals_dir, "test_schema")
    accept("test_schema", repo_root=str(tmp_path))
    binding_path, _ = accept("test_schema", repo_root=str(tmp_path), force=True)
    assert os.path.exists(binding_path)


# --- additive-only guarantee ------------------------------------------------

def test_onboarding_never_touches_real_config_directory(tmp_path, monkeypatch):
    """The property the user explicitly asked for: Phase 5 must not
    impact current functionality. Proven structurally -- accept() only
    ever writes under the repo_root it's explicitly given, never the
    real config/ directory, when pointed at a tmp_path."""
    proposals_dir = str(tmp_path / "onboarding" / "proposals")
    monkeypatch.setattr("onboarding.accept.PROPOSALS_DIR", proposals_dir)
    _write_fake_proposal(proposals_dir, "isolated_schema")
    accept("isolated_schema", repo_root=str(tmp_path))

    real_binding_path = os.path.join(REPO_ROOT, "config", "bindings", "isolated_schema.yaml")
    assert not os.path.exists(real_binding_path)
