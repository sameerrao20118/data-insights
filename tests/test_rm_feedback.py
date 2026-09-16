"""
A3 verification for datainsights/rm_feedback.py -- the D6 feedback
capture store, keyed by recommendation_id, reusing the live RM response
taxonomy verbatim.
"""

import pytest

from datainsights.rm_feedback import RESPONSE_ACTIONS, connect, feedback_for_recommendation, record_feedback


def test_record_and_read_back(tmp_path):
    db_path = str(tmp_path / "feedback.db")
    with connect(db_path) as con:
        record_feedback(con, recommendation_id="rec1", prty_id="P1", response="Customer Engaged",
                        sub_reason="wants a call next week", recorded_by="test")
    with connect(db_path) as con:
        rows = feedback_for_recommendation(con, "rec1")
    assert len(rows) == 1
    assert rows[0]["response"] == "Customer Engaged"
    assert rows[0]["sub_reason"] == "wants a call next week"


def test_rejects_response_outside_the_live_taxonomy(tmp_path):
    db_path = str(tmp_path / "feedback.db")
    with connect(db_path) as con:
        with pytest.raises(ValueError):
            record_feedback(con, recommendation_id="rec1", prty_id="P1", response="Sounds great!")


def test_multiple_responses_for_the_same_recommendation_ordered_newest_first(tmp_path):
    db_path = str(tmp_path / "feedback.db")
    with connect(db_path) as con:
        record_feedback(con, recommendation_id="rec1", prty_id="P1", response="Remind Me Later")
        record_feedback(con, recommendation_id="rec1", prty_id="P1", response="Customer Engaged")
    with connect(db_path) as con:
        rows = feedback_for_recommendation(con, "rec1")
    assert len(rows) == 2
    assert rows[0]["response"] == "Customer Engaged"  # most recent first


def test_feedback_for_different_recommendation_is_isolated(tmp_path):
    db_path = str(tmp_path / "feedback.db")
    with connect(db_path) as con:
        record_feedback(con, recommendation_id="rec1", prty_id="P1", response="Not Appropriate")
    with connect(db_path) as con:
        assert feedback_for_recommendation(con, "rec2") == []


def test_all_live_taxonomy_values_accepted(tmp_path):
    db_path = str(tmp_path / "feedback.db")
    with connect(db_path) as con:
        for response in RESPONSE_ACTIONS:
            record_feedback(con, recommendation_id="rec1", prty_id="P1", response=response)
    with connect(db_path) as con:
        rows = feedback_for_recommendation(con, "rec1")
    assert len(rows) == len(RESPONSE_ACTIONS)
