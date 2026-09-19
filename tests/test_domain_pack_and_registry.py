"""
R4 (docs/refactor_plan.md): the semantic model is a loaded registry with
typed concepts and `kind`, grouped into a domain pack; the onboarding
proposer reads the registry (no Python copy); an uncovered table yields a
PROPOSED concept for review, never a silent "unavailable".
"""

from __future__ import annotations

import os

from datainsights.packs import load_pack, pack_names, validate_pack
from datainsights.semantic.binding import CONCEPT_KINDS, load_semantic_model
from onboarding.binding_proposer import ConceptProposal, semantic_concepts
from onboarding.concept_proposer import propose_concepts, uncovered_tables
from onboarding.ml_profiler import MeasureEligibility
from onboarding.profiler import ColumnProfile, TableProfile
from tests._stub_model import FailingModel

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_concept_is_typed_with_a_known_kind():
    model = load_semantic_model()
    assert len(model.concepts) >= 7
    for name, spec in model.concepts.items():
        assert spec.kind in CONCEPT_KINDS, name
        assert spec.field_names


def test_proposer_reads_the_registry_not_a_python_copy():
    assert semantic_concepts() == {n: s.field_names for n, s in load_semantic_model().concepts.items()}
    with open(os.path.join(REPO_ROOT, "onboarding", "binding_proposer.py")) as f:
        assert "SEMANTIC_CONCEPTS" not in f.read()


def test_banking_pack_names_only_declared_things():
    assert "banking" in pack_names()
    pack = load_pack("banking")
    assert validate_pack(pack) == []
    assert set(pack.concepts) == set(load_semantic_model().concepts)


def _profiles():
    return {
        "covenant_tests": TableProfile(
            physical_table="covenant_tests", row_count=900,
            columns=[ColumnProfile(name="AGRMNT_ID", inferred_type="string", nullable=False, is_unique=False),
                     ColumnProfile(name="HEADROOM_PCT", inferred_type="float", nullable=False, is_unique=False),
                     ColumnProfile(name="TEST_DT", inferred_type="date", nullable=False, is_unique=False)],
            key_candidates=[], date_columns=["TEST_DT"], bitemporal_pair=None),
        "branch_lookup": TableProfile(
            physical_table="branch_lookup", row_count=40,
            columns=[ColumnProfile(name="BRANCH_ID", inferred_type="string", nullable=False, is_unique=True)],
            key_candidates=["BRANCH_ID"], date_columns=[], bitemporal_pair=None),
        "parties": TableProfile(physical_table="parties", row_count=100, columns=[
            ColumnProfile(name="party_id", inferred_type="string", nullable=False, is_unique=True)],
            key_candidates=["party_id"], date_columns=[], bitemporal_pair=None),
    }


def _binding_proposals():
    return {"Party": ConceptProposal(concept="Party", entity="parties", field_mappings={"party_id": "party_id"},
                                     confidence=0.9, evidence="", unavailable_reason=None)}


def _eligibility():
    return {"covenant_tests": [MeasureEligibility(table="covenant_tests", column="HEADROOM_PCT", eligible=True,
                                                  entity_column="AGRMNT_ID", n_entities=60,
                                                  observations_per_entity=15.0)],
            "branch_lookup": [], "parties": []}


def test_uncovered_table_with_eligible_measures_is_a_concept_candidate_a_lookup_is_not():
    assert uncovered_tables(_profiles(), _binding_proposals(), _eligibility()) == ["covenant_tests"]


def test_uncovered_table_yields_a_proposal_for_review_never_a_silent_drop():
    proposals = propose_concepts(_profiles(), _binding_proposals(), _eligibility(), FailingModel())
    assert [p.table for p in proposals] == ["covenant_tests"]
    assert proposals[0].accepted is False
    assert "proposal failed" in proposals[0].rejected_reason  # visible in review.md, not hidden
