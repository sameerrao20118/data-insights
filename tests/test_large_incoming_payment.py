"""
Tests for detection_engine/large_incoming_payment.py, covering the cases
listed in docs/detector_spec_large_incoming_payment.md section
"Failure modes / test cases". Uses hand-built synthetic frames, NOT
data_generator output and NOT trigger_events.csv -- independent expected
outputs, not tests that mirror implementation logic.
"""

import uuid
from datetime import date, timedelta

import pandas as pd

from detection_engine.large_incoming_payment import (
    DetectorConfig,
    apply_cooldown,
    detect,
)

CFG = DetectorConfig(
    baseline_window_days=90,
    min_baseline_transactions=15,
    mad_multiplier=6.0,
    absolute_floor_by_currency={"EUR": 5000},
    cooldown_days=30,
    rule_version="test.v1",
)

STEP_DAYS = 2


def make_tx(account_id, client_id, d: date, amount, currency="EUR", direction="credit"):
    return {
        "transaction_id": str(uuid.uuid4()),
        "account_id": account_id,
        "client_id": client_id,
        "booking_date": d.isoformat(),
        "amount": amount,
        "currency": currency,
        "direction": direction,
    }


def regular_history(account_id, client_id, n, start: date, amount=1000.0, step_days=STEP_DAYS):
    return [make_tx(account_id, client_id, start + timedelta(days=i * step_days), amount)
            for i in range(n)]


def candidate_date_within_window(start: date, n: int, step_days=STEP_DAYS, margin_days=7) -> date:
    """A candidate date shortly after the last history row, and still well
    inside the 90-day trailing baseline window relative to day 0 of the
    history -- so tests actually exercise 'has a full baseline', not
    accidentally 'baseline expired out of window'."""
    last_history_day = (n - 1) * step_days
    return start + timedelta(days=last_history_day + margin_days)


def test_below_minimum_history_is_insufficient_evidence():
    # exactly 14 prior transactions before the candidate -> below min of 15
    start = date(2024, 1, 1)
    n = 14
    rows = regular_history("A1", "C1", n, start)
    candidate_date = candidate_date_within_window(start, n)
    rows.append(make_tx("A1", "C1", candidate_date, 50000.0))
    df = pd.DataFrame(rows)

    out = detect(df, CFG, run_id="t")
    hit = out[out["transaction_id"] == rows[-1]["transaction_id"]]
    assert len(hit) == 1
    assert hit.iloc[0]["status"] == "insufficient_evidence"


def test_exactly_minimum_history_is_eligible():
    start = date(2024, 1, 1)
    n = 15
    rows = regular_history("A2", "C2", n, start)
    candidate_date = candidate_date_within_window(start, n)
    rows.append(make_tx("A2", "C2", candidate_date, 50000.0))
    df = pd.DataFrame(rows)

    out = detect(df, CFG, run_id="t")
    hit = out[out["transaction_id"] == rows[-1]["transaction_id"]]
    assert len(hit) == 1
    assert hit.iloc[0]["status"] == "detected"
    assert hit.iloc[0]["baseline_n"] == 15


def test_below_absolute_floor_never_detected_even_if_statistically_extreme():
    start = date(2024, 1, 1)
    n = 20
    # tight baseline around 10 EUR so a jump to e.g. 200 EUR is statistically
    # huge in MAD terms, but well below the 5000 EUR floor
    rows = regular_history("A3", "C3", n, start, amount=10.0)
    candidate_date = candidate_date_within_window(start, n)
    rows.append(make_tx("A3", "C3", candidate_date, 200.0))
    df = pd.DataFrame(rows)

    out = detect(df, CFG, run_id="t")
    hit = out[out["transaction_id"] == rows[-1]["transaction_id"]]
    # floor blocks detection -> not even in the (detected/insufficient_evidence) output
    assert hit.empty


def test_rerun_on_identical_snapshot_is_idempotent_by_detection_id():
    start = date(2024, 1, 1)
    n = 20
    rows = regular_history("A4", "C4", n, start)
    candidate_date = candidate_date_within_window(start, n)
    rows.append(make_tx("A4", "C4", candidate_date, 50000.0))
    df = pd.DataFrame(rows)

    out1 = detect(df, CFG, run_id="run1")
    out2 = detect(df, CFG, run_id="run2")
    ids1 = set(out1[out1.status == "detected"]["detection_id"])
    ids2 = set(out2[out2.status == "detected"]["detection_id"])
    assert ids1 == ids2 and len(ids1) == 1  # same transaction -> same detection_id


def test_all_debit_account_never_falsely_detected():
    start = date(2024, 1, 1)
    rows = [make_tx("A5", "C5", start + timedelta(days=i), 1000.0, direction="debit")
            for i in range(30)]
    df = pd.DataFrame(rows)
    out = detect(df, CFG, run_id="t")
    assert out.empty


def test_multi_currency_baselines_are_independent():
    start = date(2024, 1, 1)
    n = 20
    eur_rows = regular_history("A6", "C6", n, start, amount=1000.0)
    usd_rows = regular_history("A6", "C6", n, start, amount=100000.0)
    for r in usd_rows:
        r["currency"] = "USD"
    candidate_date = candidate_date_within_window(start, n)
    # a EUR-sized anomaly must not be inferred from the USD history
    eur_candidate = make_tx("A6", "C6", candidate_date, 50000.0, currency="EUR")
    df = pd.DataFrame(eur_rows + usd_rows + [eur_candidate])

    out = detect(df, CFG, run_id="t")
    eur_hit = out[out["transaction_id"] == eur_candidate["transaction_id"]]
    assert len(eur_hit) == 1
    assert eur_hit.iloc[0]["status"] == "detected"
    assert eur_hit.iloc[0]["baseline_n"] == 20  # only the 20 EUR rows, not 40


def test_no_future_leakage_baseline_excludes_same_and_later_rows():
    # a single huge transaction on day 0 with nothing before it must be
    # insufficient_evidence, never "detected against itself"
    df = pd.DataFrame([make_tx("A7", "C7", date(2024, 1, 1), 500000.0)])
    out = detect(df, CFG, run_id="t")
    hit = out[out["transaction_id"] == df.iloc[0]["transaction_id"]]
    assert len(hit) == 1
    assert hit.iloc[0]["status"] == "insufficient_evidence"


def test_cooldown_suppresses_second_detection_within_window_keeps_first_active():
    start = date(2024, 1, 1)
    n = 20
    rows = regular_history("A8", "C8", n, start)
    d1 = candidate_date_within_window(start, n)
    d2 = d1 + timedelta(days=10)  # within 30-day cooldown of d1
    tx1 = make_tx("A8", "C8", d1, 50000.0)
    tx2 = make_tx("A8", "C8", d2, 60000.0)
    rows.append(tx1)
    rows.append(tx2)
    df = pd.DataFrame(rows)

    out = detect(df, CFG, run_id="t")
    out = apply_cooldown(out, CFG)
    candidates = out[out["transaction_id"].isin([tx1["transaction_id"], tx2["transaction_id"]])]
    statuses = candidates.sort_values("event_date")["status"].tolist()
    assert statuses == ["detected", "suppressed_cooldown"]


def test_cooldown_does_not_suppress_detection_outside_window():
    start = date(2024, 1, 1)
    n = 20
    rows = regular_history("A9", "C9", n, start)
    d1 = candidate_date_within_window(start, n)
    d2 = d1 + timedelta(days=45)  # outside 30-day cooldown
    tx1 = make_tx("A9", "C9", d1, 50000.0)
    tx2 = make_tx("A9", "C9", d2, 60000.0)
    rows.append(tx1)
    rows.append(tx2)
    df = pd.DataFrame(rows)

    out = detect(df, CFG, run_id="t")
    out = apply_cooldown(out, CFG)
    candidates = out[out["transaction_id"].isin([tx1["transaction_id"], tx2["transaction_id"]])]
    statuses = candidates.sort_values("event_date")["status"].tolist()
    assert statuses == ["detected", "detected"]
