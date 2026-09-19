"""
SLOT E4 readiness verification (docs/generalization_plan.md Phase 4):
datainsights/ml/label_pipeline.py's join is point-in-time correct by
construction (it never recomputes a worklist row, only joins onto it),
and datainsights/ml/model_registry.py round-trips a model + card, and
refuses to save one without a complete card.
"""

from __future__ import annotations


import pandas as pd
import pytest

from datainsights.ml.label_pipeline import TRAINING_TABLE_COLUMNS, build_training_table
from datainsights.ml.model_registry import ModelCard, ModelRegistryError, list_models, load_card, load_model, save_model
from datainsights.rm_feedback import connect as connect_feedback
from datainsights.rm_feedback import record_feedback

WORKLIST_COLUMNS = [
    "recommendation_id", "prty_id", "nba_category", "signal_strength",
    "confirming_domains", "endogenous_signal_type", "sizing_basis",
    "indicative_revenue_eur", "indicative_offer_eur",
]


def _worklist_row(**overrides):
    row = dict(recommendation_id="rec1", prty_id="PRTY00001", nba_category="FINANCING_NEED",
              signal_strength=3, confirming_domains="deposits", endogenous_signal_type="cash_buildup",
              sizing_basis="balance_buildup_amount_illustrative", indicative_revenue_eur=1000.0,
              indicative_offer_eur=50000.0)
    row.update(overrides)
    return row


# --- label pipeline ---------------------------------------------------------

def test_empty_feedback_produces_empty_shaped_table(tmp_path):
    worklist = pd.DataFrame([_worklist_row()])
    with connect_feedback(str(tmp_path / "fb.db")) as con:
        table = build_training_table(con, worklist)
    assert table.empty
    assert list(table.columns) == TRAINING_TABLE_COLUMNS


def test_feedback_joins_onto_its_worklist_row(tmp_path):
    worklist = pd.DataFrame([_worklist_row(recommendation_id="rec1"),
                             _worklist_row(recommendation_id="rec2", prty_id="PRTY00002")])
    with connect_feedback(str(tmp_path / "fb.db")) as con:
        record_feedback(con, recommendation_id="rec1", prty_id="PRTY00001",
                        response="Customer Engaged", sub_reason="interested")
        table = build_training_table(con, worklist)
    assert len(table) == 1
    assert table.iloc[0]["response"] == "Customer Engaged"
    assert table.iloc[0]["nba_category"] == "FINANCING_NEED"  # joined from the worklist row, not invented
    assert table.iloc[0]["signal_strength"] == 3


def test_feedback_with_no_matching_worklist_row_is_dropped_not_fabricated(tmp_path):
    """A feedback row for a recommendation_id the worklist doesn't have
    (e.g. an old run) must be excluded, never joined to a wrong or blank
    row."""
    worklist = pd.DataFrame([_worklist_row(recommendation_id="rec1")])
    with connect_feedback(str(tmp_path / "fb.db")) as con:
        record_feedback(con, recommendation_id="rec_unknown", prty_id="PRTY00099",
                        response="Not Appropriate")
        table = build_training_table(con, worklist)
    assert table.empty


def test_multiple_responses_for_one_recommendation_each_become_a_row(tmp_path):
    worklist = pd.DataFrame([_worklist_row(recommendation_id="rec1")])
    with connect_feedback(str(tmp_path / "fb.db")) as con:
        record_feedback(con, recommendation_id="rec1", prty_id="PRTY00001", response="Remind Me Later")
        record_feedback(con, recommendation_id="rec1", prty_id="PRTY00001", response="Customer Engaged")
        table = build_training_table(con, worklist)
    assert len(table) == 2
    assert set(table["response"]) == {"Remind Me Later", "Customer Engaged"}


# --- model registry ----------------------------------------------------------

def _valid_card(**overrides):
    fields = dict(purpose="test", data_window="2026-01-01..2026-03-01", features=["signal_strength"],
                 metrics={"auc": 0.5}, limitations="toy model for registry round-trip only", owner="test-suite")
    fields.update(overrides)
    return ModelCard(**fields)


def test_save_and_load_round_trip(tmp_path):
    root = str(tmp_path / "models")
    model = {"toy": "not a real model, just a picklable object"}
    out_dir = save_model("toy_model", "v1", model, _valid_card(), root=root)
    assert out_dir.endswith(("toy_model/v1", "toy_model\\v1"))

    loaded = load_model("toy_model", "v1", root=root)
    assert loaded == model
    card = load_card("toy_model", "v1", root=root)
    assert card.owner == "test-suite"
    assert card.features == ["signal_strength"]


def test_incomplete_card_refused():
    with pytest.raises(ModelRegistryError, match="owner"):
        save_model("x", "v1", {}, _valid_card(owner=""), root="/tmp/unused")


def test_load_missing_model_raises_clearly(tmp_path):
    with pytest.raises(ModelRegistryError, match="no model registered"):
        load_model("nonexistent", "v1", root=str(tmp_path))


def test_list_models_empty_registry_is_not_an_error(tmp_path):
    assert list_models(root=str(tmp_path / "does_not_exist")) == []


def test_list_models_reports_every_registered_version(tmp_path):
    root = str(tmp_path / "models")
    save_model("model_a", "v1", {"x": 1}, _valid_card(), root=root)
    save_model("model_a", "v2", {"x": 2}, _valid_card(), root=root)
    save_model("model_b", "v1", {"y": 1}, _valid_card(), root=root)
    assert set(list_models(root=root)) == {("model_a", "v1"), ("model_a", "v2"), ("model_b", "v1")}
