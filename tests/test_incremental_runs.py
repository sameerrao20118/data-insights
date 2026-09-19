"""
R19 (run lock + one run table), R11 (incremental runs with a watermark),
R12 (a new extracted event enqueues exactly the clients it qualifies), and
the clock (datainsights/monitor.py) -- docs/refactor_plan.md §3 / §6g.
"""

from __future__ import annotations

import os
import shutil
from datetime import date, timedelta

import pandas as pd
import pytest

from datainsights import monitor, runs
from external_events.event_extraction_agent import ExtractionResult
from external_events.exposure_qualifier import ExogenousEvent, load_events
from external_events.extracted_event_store import store_result

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
EVENTS = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")

pytestmark = pytest.mark.skipif(not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS)), reason="FDM data not generated")


@pytest.fixture
def book(tmp_path):
    """A private copy of the FDM book, a private runs db, no extracted events."""
    data = tmp_path / "fdm"
    shutil.copytree(FDM_DIR, data)
    return {"data_dir": str(data), "runs_db": str(tmp_path / "runs.db"), "events": str(tmp_path / "extracted.csv")}


def _run(book, **kw):
    return runs.run_book("fdm_local", data_dir_override=book["data_dir"], runs_db=book["runs_db"],
                         events_path=book["events"], write_outputs=False, **kw)


def _rec_key(r):
    return (r.prty_id, r.nba_category, r.sized_offer_eur, r.hypothesis, r.recommended_action, r.signal_strength)


def test_two_runs_on_unchanged_data_evaluate_nothing_the_second_time_and_agree(book):
    first = _run(book)
    assert not first.incremental and len(first.evaluated_ids) == 60 and first.recommendations
    second = _run(book)
    assert second.incremental
    assert second.evaluated_ids == [] and len(second.carried_ids) == 60
    assert sorted(map(_rec_key, second.recommendations)) == sorted(map(_rec_key, first.recommendations))
    pd.testing.assert_frame_equal(second.worklist, first.worklist)
    assert second.seconds < first.seconds * 0.5, (first.seconds, second.seconds)  # measured 0.14s vs ~1s; plan asks <10%


def test_a_new_balance_row_for_one_client_reevaluates_exactly_that_client(book):
    """Control vs treatment on the same watermark: moving as_of forward a
    day legitimately makes that day's real rows 'new' for some clients;
    the appended balance row must add EXACTLY one more client to that set."""
    first = _run(book)
    control_db = book["runs_db"] + ".control"
    shutil.copy(book["runs_db"], control_db)
    control = runs.run_book("fdm_local", data_dir_override=book["data_dir"], runs_db=control_db,
                            events_path=book["events"], write_outputs=False, as_of=first.as_of + timedelta(days=1))

    bal_path = os.path.join(book["data_dir"], "kernel", "agreement_daily_balance.csv")
    bal = pd.read_csv(bal_path)
    agreements = pd.read_csv(os.path.join(book["data_dir"], "kernel", "party_agreement.csv"))
    party = next(p for p in sorted(agreements["PRTY_ID"].unique()) if p not in control.evaluated_ids)
    acct = agreements[agreements["PRTY_ID"] == party]["AGRMNT_ID"].iloc[0]
    new_day = first.as_of + timedelta(days=1)
    pd.concat([bal, pd.DataFrame([{"AGRMNT_ID": acct, "AGRMNT_DLY_BAL_STRT_DTTM": new_day.isoformat(),
                                   "AGRMNT_LDGR_BAL_AMT": 123456.0, "AGRMNT_BAL_CURY_CD": "EUR"}])]
              ).to_csv(bal_path, index=False)
    treatment = _run(book, as_of=new_day)
    assert set(treatment.evaluated_ids) - set(control.evaluated_ids) == {party}, treatment.reasons.get(party)
    assert "BalanceObservation" in treatment.reasons[party]
    assert set(treatment.carried_ids) == set(control.carried_ids) - {party}


def test_a_new_extracted_event_enqueues_exactly_the_clients_it_qualifies(book):
    first = _run(book)
    feed = load_events(EVENTS)[0]
    later = ExogenousEvent(event_id="EXTRACTED-1", event_date=feed.event_date, event_type=feed.event_type,
                           affected_country=feed.affected_country, affected_sector=feed.affected_sector,
                           severity=feed.severity, estimated_value_eur=feed.estimated_value_eur)
    store_result(ExtractionResult(source_name="test", source_ref="notice-1", event=later, quote="q",
                                  confidence=0.9, status="extracted"), book["events"], book["events"] + ".review")
    second = _run(book)
    assert second.evaluated_ids, "the qualifying clients must be re-evaluated"
    assert all("qualifies" in second.reasons[p] for p in second.evaluated_ids)
    # exactly the confirmed-exposure clients, not everyone sharing the sector
    confirmed = {r.prty_id for r in first.recommendations if r.exogenous_event_type}
    assert set(second.evaluated_ids) == confirmed


def test_run_lock_refuses_an_overlapping_run_and_ignores_a_dead_one(book):
    con = runs.connect(book["runs_db"])
    with runs.RunLock(con, "fdm_local", date(2025, 10, 4), max_concurrent=1, incremental=False):
        with pytest.raises(runs.RunOverlapError):
            with runs.RunLock(con, "fdm_local", date(2025, 10, 4), max_concurrent=1, incremental=False):
                pass
    # a `running` row whose pid is dead is abandoned, not a lock
    con.execute("INSERT INTO runs (run_id, profile, as_of, started_at, status, host, pid, incremental) "
                "VALUES ('dead', 'fdm_local', '2025-10-04', 'x', 'running', ?, 999999999, 0)",
                (__import__("socket").gethostname(),))
    con.commit()
    with runs.RunLock(con, "fdm_local", date(2025, 10, 4), max_concurrent=1, incremental=False):
        pass
    statuses = dict(con.execute("SELECT run_id, status FROM runs").fetchall())
    assert statuses["dead"] == "abandoned"
    assert list(statuses.values()).count("finished") == 2


def test_failed_run_is_recorded_not_left_running(book):
    con = runs.connect(book["runs_db"])
    with pytest.raises(ValueError):
        with runs.RunLock(con, "fdm_local", date(2025, 10, 4), max_concurrent=1, incremental=False):
            raise ValueError("boom")
    row = con.execute("SELECT status, error FROM runs").fetchone()
    assert row[0] == "failed" and "boom" in row[1]


def test_monitor_reads_the_profile_block_and_ticks_on_the_clock(book, monkeypatch):
    slept = []
    enabled = monitor.active_profile("fdm_local").model_copy(deep=True)
    enabled.monitor.enabled = True
    monkeypatch.setattr(monitor, "active_profile", lambda name=None: enabled)
    monkeypatch.setattr(monitor, "run_book", lambda profile, **kw: _run(book, **{k: v for k, v in kw.items()
                                                                               if k in ("as_of", "lookback_days")}))
    results = monitor.run_loop("fdm_local", max_iterations=2, sleep=slept.append,
                               as_of_fn=lambda: date(2025, 10, 4), log=lambda *_: None)
    assert len(results) == 2 and slept == [60 * 60]  # interval_minutes: 60
    assert results[1].incremental and results[1].evaluated_ids == []
    assert monitor.lookback_days_from_ref({"cash_buildup": {"window_days": 60}}, "cash_buildup.window_days") == 60
    with pytest.raises(ValueError):
        monitor.lookback_days_from_ref({}, "nope.days")


def test_monitor_refuses_when_disabled_unless_once():
    with pytest.raises(RuntimeError, match="monitor.enabled"):
        monitor.run_loop("fdm_local", max_iterations=1, sleep=lambda s: None, log=lambda *_: None,
                         as_of_fn=lambda: date(2025, 10, 4))
