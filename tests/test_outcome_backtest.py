"""
R18: replay as of T with a virtual clock, join RM feedback recorded
strictly after T, report hit rate by category.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import pytest

from datainsights import backtest as bt
from datainsights.rm_feedback import SCHEMA, connect, record_feedback
from external_events.exposure_qualifier import load_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
EVENTS = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")
pytestmark = pytest.mark.skipif(not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS)), reason="FDM data not generated")


def test_hit_rate_by_category_counts_only_feedback_recorded_after_T(tmp_path):
    T = load_events(EVENTS)[0].event_date + timedelta(days=90)
    db = str(tmp_path / "fb.db")
    report0, detail0 = bt.backtest_one("fdm_local", T, feedback_db=db, events_path=None)
    assert len(detail0) > 3 and report0["n_with_outcome"].sum() == 0 and report0["hit_rate"].isna().all()

    cat = report0.sort_values("n_recommended", ascending=False).iloc[0]["nba_category"]
    ids = list(detail0[detail0["nba_category"] == cat]["recommendation_id"])[:3]
    with connect(db) as con:
        record_feedback(con, recommendation_id=ids[0], prty_id="x", response="Customer Engaged")
        record_feedback(con, recommendation_id=ids[1], prty_id="x", response="Not Appropriate")
        record_feedback(con, recommendation_id=ids[2], prty_id="x", response="Customer Engaged")
    # a response recorded BEFORE T must never count -- virtual clock discipline
    with sqlite3.connect(db) as con:
        con.executescript(SCHEMA)
        con.execute("UPDATE rm_feedback SET recorded_at = ? WHERE recommendation_id = ?",
                    ((T - timedelta(days=5)).isoformat() + "T00:00:00+00:00", ids[2]))
    report, detail = bt.backtest_one("fdm_local", T, feedback_db=db, events_path=None)
    row = report[report["nba_category"] == cat].iloc[0]
    assert row["n_with_outcome"] == 2 and row["n_engaged"] == 1 and row["hit_rate"] == 0.5
    assert detail[detail["recommendation_id"] == ids[2]]["response"].isna().all()


def test_recommendation_ids_are_stable_so_the_join_is_meaningful():
    T = load_events(EVENTS)[0].event_date + timedelta(days=90)
    _, a = bt.backtest_one("fdm_local", T, feedback_db="/nonexistent.db", events_path=None, party_limit=15)
    _, b = bt.backtest_one("fdm_local", T, feedback_db="/nonexistent.db", events_path=None, party_limit=15)
    assert list(a["recommendation_id"]) == list(b["recommendation_id"]) and len(a)


def test_an_event_dated_after_T_is_not_visible_at_T():
    ev = load_events(EVENTS)[0]
    before = ev.event_date - timedelta(days=1)
    _, detail = bt.backtest_one("fdm_local", before, feedback_db="/nonexistent.db", events_path=None, party_limit=20)
    assert not any(detail.get("nba_category", []).empty for _ in [0])  # frame exists
    from datainsights.runtime import build_runtime
    assert bt._events_visible_at(build_runtime("fdm_local"), before, None) is None
    assert bt._events_visible_at(build_runtime("fdm_local"), ev.event_date, None).event_id == ev.event_id


def test_date_range_is_inclusive_and_stepped():
    assert bt.date_range(date(2025, 1, 1), date(2025, 3, 1), 30) == [date(2025, 1, 1), date(2025, 1, 31), date(2025, 3, 2)][:2]
    assert bt.date_range(date(2025, 1, 1), date(2025, 1, 1), 7) == [date(2025, 1, 1)]
