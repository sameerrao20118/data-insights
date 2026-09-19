"""
M2 verification for datainsights/sources/fdm_local.py: protected-path
refusal (mirroring OfflineLocalSource's existing test), FinCrime table
refusal (docs/decision_record.md D5), and as-at correctness against the
generated FDM dataset.
"""

import os
from datetime import date

import pandas as pd
import pytest

from datainsights.sources.base import DataSourceError
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")

pytestmark = pytest.mark.skipif(
    not os.path.isdir(OUT_DIR),
    reason="data_generator/output_fdm/ not generated -- run "
           "`python -m data_generator.fdm.generate_fdm` first",
)


@pytest.fixture
def source() -> FdmLocalSource:
    return FdmLocalSource(OUT_DIR, CONTRACT_PATH)


def test_refuses_protected_root_path():
    with pytest.raises(DataSourceError, match="protected"):
        FdmLocalSource(
            os.path.join(OUT_DIR, "protected_evaluator_only"), CONTRACT_PATH
        )


@pytest.mark.parametrize("physical_table", [
    "fsa_prd_fincrime", "FSA_PRD_FINCRIME", "fsa_prd_fc_analytics",
    "pep_prs", "sam_alerts_hist_t_cm_xdo",
])
def test_refuses_fincrime_table_references(source, physical_table):
    with pytest.raises(DataSourceError, match="FinCrime"):
        source._csv_path(physical_table, "kernel")


def test_unknown_entity_rejected(source):
    with pytest.raises(DataSourceError):
        source.read_entity("PARTY_METRIC")


def test_as_at_on_non_bitemporal_entity_rejected(source):
    with pytest.raises(DataSourceError, match="not bi-temporal"):
        source.as_at("PARTY_DEMOGRAPHIC", date(2025, 1, 1), ["PRTY_ID"])


def test_as_at_party_returns_exactly_one_version_per_key(source):
    snapshot = source.party(date(2026, 3, 1))
    assert not snapshot["PRTY_ID"].duplicated().any()
    assert len(snapshot) > 0


def test_as_at_reflects_a_downgrade_over_time(source):
    """Pick a party with 2+ versions (the generator guarantees at least
    one exists -- see test_fdm_generator_contract.py) and confirm
    as_at() actually returns different rows before/after the change,
    not the same (e.g. always-latest) row regardless of date."""
    full = pd.read_csv(os.path.join(OUT_DIR, "kernel", "party.csv"))
    counts = full.groupby("PRTY_ID").size()
    multi_id = counts[counts >= 2].index[0]
    versions = full[full["PRTY_ID"] == multi_id].sort_values("EFFECTIVE_START_DT")
    v1, v2 = versions.iloc[0], versions.iloc[1]
    change_date = date.fromisoformat(v2["EFFECTIVE_START_DT"])

    before = source.as_at("PARTY", change_date - pd.Timedelta(days=1), ["PRTY_ID"])
    after = source.as_at("PARTY", change_date, ["PRTY_ID"])

    before_row = before[before["PRTY_ID"] == multi_id].iloc[0]
    after_row = after[after["PRTY_ID"] == multi_id].iloc[0]
    assert before_row["RSK_GRD_VAL"] == v1["RSK_GRD_VAL"]
    assert after_row["RSK_GRD_VAL"] == v2["RSK_GRD_VAL"]
    assert before_row["RSK_GRD_VAL"] != after_row["RSK_GRD_VAL"]


def test_collateral_coverage_as_at_reflects_a_value_drop(source):
    values = pd.read_csv(os.path.join(OUT_DIR, "lending", "collateral_item_value.csv"))
    counts = values.groupby("CLTRL_ITEM_ID").size()
    multi_id = counts[counts >= 2].index[0]
    versions = values[values["CLTRL_ITEM_ID"] == multi_id].sort_values("EFFECTIVE_START_DT")
    v2 = versions.iloc[1]  # the second version's start is the change date; v1 is not needed
    change_date = date.fromisoformat(v2["EFFECTIVE_START_DT"])

    before = source.collateral_item_value(change_date - pd.Timedelta(days=1))
    after = source.collateral_item_value(change_date)

    before_val = before[before["CLTRL_ITEM_ID"] == multi_id].iloc[0]["CLTRL_VAL_AMT"]
    after_val = after[after["CLTRL_ITEM_ID"] == multi_id].iloc[0]["CLTRL_VAL_AMT"]
    assert after_val < before_val, "expected the generator's simulated coverage drop"


def test_slot_a2_treasury_raises_not_implemented(source):
    with pytest.raises(NotImplementedError, match="SLOT A2"):
        source.treasury_position(date(2026, 1, 1))


def test_slot_a1_risk_measure_raises_not_implemented(source):
    with pytest.raises(NotImplementedError, match="SLOT A1"):
        source.risk_measure(date(2026, 1, 1))
