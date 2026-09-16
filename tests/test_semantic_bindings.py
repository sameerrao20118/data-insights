"""
Phase 1 verification (docs/generalization_plan.md): the FDM binding
validates against its own contract, and CanonicalSource reads real
generated data correctly through it -- renames, derived/mapped fields,
single- and two-hop joins, bi-temporal as-at collapse vs. full version
history, and per-key filtering.

This is purely additive: nothing in detection_engine/, agents/, or
external_events/ reads through CanonicalSource yet (see
docs/generalization_plan.md's Phase 1 status note for what's deferred).
These tests prove the foundation works in isolation, against real data,
before that rewire happens.
"""

import os
from datetime import date

import pytest

from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource, CanonicalSourceError
from datainsights.semantic.validate import validate_binding
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")

pytestmark = pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")


@pytest.fixture(scope="module")
def binding():
    return load_binding("fdm")


@pytest.fixture(scope="module")
def canonical(binding):
    source = FdmLocalSource(FDM_DIR, os.path.join(REPO_ROOT, "config", "entities_fdm.yaml"))
    return CanonicalSource(source, binding)


def test_fdm_binding_validates_clean(binding):
    assert validate_binding(binding, repo_root=REPO_ROOT) == []


def test_a_binding_with_a_wrong_column_fails_validation(binding):
    broken = binding.model_copy(deep=True)
    broken.concepts["Party"].fields["party_id"] = "NOT_A_REAL_COLUMN"
    problems = validate_binding(broken, repo_root=REPO_ROOT)
    assert any("NOT_A_REAL_COLUMN" in p for p in problems)


def test_party_reads_renamed_and_joined_fields(canonical):
    parties = canonical.read("Party", as_at=date(2025, 10, 4))
    assert set(parties.columns) >= {"party_id", "segment", "sector_code", "sector_name",
                                    "country_code", "high_risk_flag"}
    assert parties["high_risk_flag"].dtype == bool
    assert not parties.empty


def test_account_derived_product_class_and_currency(canonical):
    accounts = canonical.read("Account", as_at=date(2025, 10, 4), party_id="PRTY00036")
    assert not accounts.empty
    assert set(accounts["product_class"]) <= {"deposit", "facility", "mortgage", "other"}
    assert (accounts["currency"] == "EUR").all()


def test_account_filtered_by_party_id_only_returns_that_party(canonical):
    accounts = canonical.read("Account", as_at=date(2025, 10, 4), party_id="PRTY00036")
    assert set(accounts["party_id"]) == {"PRTY00036"}


def test_balance_observation_reads_real_history(canonical):
    accounts = canonical.read("Account", as_at=date(2025, 10, 4), party_id="PRTY00036")
    deposit = accounts[accounts["product_class"] == "deposit"].iloc[0]
    bals = canonical.read("BalanceObservation", account_id=deposit["account_id"])
    assert len(bals) > 1
    assert {"account_id", "observed_at", "balance"} <= set(bals.columns)


def test_risk_grade_version_returns_full_bitemporal_history_not_collapsed(canonical):
    """versions() must keep every effective-dated row (needed by any
    detector reading history, e.g. rating_downgrade) -- as-at collapse is
    read()'s job with as_at=..., not versions()'s."""
    versions = canonical.versions("RiskGradeVersion", party_id="PRTY00036")
    assert len(versions) >= 1
    assert {"valid_from", "valid_to", "grade_code", "grade_value"} <= set(versions.columns)


def test_risk_grade_version_as_at_collapses_to_one_row_per_party(canonical):
    snapshot = canonical.read("RiskGradeVersion", as_at=date(2025, 10, 4))
    assert not snapshot["party_id"].duplicated().any()
    assert "valid_from" not in snapshot.columns  # dropped after collapse -- redundant on one row


def test_as_at_never_leaks_a_future_version():
    """The exact bi-temporal proof docs/decision_record.md calls load-
    bearing: an as-at date before a real version change must not see it."""
    source = FdmLocalSource(FDM_DIR, os.path.join(REPO_ROOT, "config", "entities_fdm.yaml"))
    cs = CanonicalSource(source, load_binding("fdm"))
    versions = cs.versions("RiskGradeVersion", party_id="PRTY00036")
    if len(versions) < 2:
        pytest.skip("PRTY00036 has no recorded grade change in this generated dataset")
    change_date = versions["valid_from"].max().date()
    before = cs.read("RiskGradeVersion", as_at=change_date - __import__("datetime").timedelta(days=1),
                     party_id="PRTY00036")
    after = cs.read("RiskGradeVersion", as_at=change_date, party_id="PRTY00036")
    assert before.iloc[0]["grade_value"] != after.iloc[0]["grade_value"]


def test_collateral_valuation_two_hop_join(canonical):
    """CollateralValuation's binding chains two joins where the second
    join's key (AGRMNT_ID) only exists after the first join runs -- the
    exact case that broke naive per-join validation until fixed."""
    cv = canonical.read("CollateralValuation")
    assert not cv.empty
    assert {"collateral_id", "account_id", "value", "original_limit"} <= set(cv.columns)


def test_unavailable_concept_reports_a_reason_not_a_crash(canonical):
    assert canonical.available("PartyMetricVersion") is False
    assert "PARTY_METRIC" in canonical.unavailable_reason("PartyMetricVersion")
    with pytest.raises(CanonicalSourceError, match="PARTY_METRIC"):
        canonical.read("PartyMetricVersion")


def test_unknown_concept_raises_clearly(canonical):
    with pytest.raises(CanonicalSourceError, match="not in binding"):
        canonical.read("SomeConceptThatDoesNotExist")
