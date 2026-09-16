"""Tests for detection_engine/cash_buildup.py. Hand-built frames, not
generator output -- independent expected outputs."""

from datetime import date, timedelta

import pandas as pd
import pytest

from detection_engine.cash_buildup import DetectorConfig, apply_cooldown, detect

CFG = DetectorConfig(
    window_days=60, min_increase_pct=0.15, min_prior_balance=5000,
    cooldown_days=45, rule_version="test.v1",
)


def bal_row(agrmnt_id, prty_id, d: date, amount):
    return {"PRTY_ID": prty_id, "AGRMNT_ID": agrmnt_id,
            "AGRMNT_DLY_BAL_STRT_DTTM": d.isoformat(), "AGRMNT_LDGR_BAL_AMT": amount}


def test_no_prior_observation_is_insufficient_evidence():
    start = date(2024, 1, 1)
    rows = [bal_row("A1", "P1", start, 10000)]
    result = detect(pd.DataFrame(rows), CFG, "run1")
    assert list(result["status"]) == ["insufficient_evidence"]


def test_sustained_increase_over_window_is_detected():
    start = date(2024, 1, 1)
    rows = [bal_row("A1", "P1", start, 10000),
            bal_row("A1", "P1", start + timedelta(days=61), 12000)]  # +20%
    result = detect(pd.DataFrame(rows), CFG, "run1")
    detected = result[result["status"] == "detected"]
    assert len(detected) == 1
    assert abs(detected.iloc[0]["increase_pct"] - 0.2) < 1e-6


def test_below_threshold_increase_not_detected():
    start = date(2024, 1, 1)
    rows = [bal_row("A1", "P1", start, 10000),
            bal_row("A1", "P1", start + timedelta(days=61), 10500)]  # +5%
    result = detect(pd.DataFrame(rows), CFG, "run1")
    assert "detected" not in list(result["status"])


def test_small_prior_balance_excluded():
    """The day-0 row legitimately emits insufficient_evidence regardless
    of balance size (no prior observation exists yet) -- what this test
    actually checks is that the day-61 row, which DOES have a prior
    observation but one below min_prior_balance, is excluded rather than
    flagged as a 100% "buildup" off a trivial base."""
    start = date(2024, 1, 1)
    rows = [bal_row("A1", "P1", start, 1000),
            bal_row("A1", "P1", start + timedelta(days=61), 2000)]  # +100% but tiny base
    result = detect(pd.DataFrame(rows), CFG, "run1")
    assert "detected" not in list(result["status"])
    assert len(result) == 1  # only the day-0 insufficient_evidence row


def test_cooldown_suppresses_repeat_detection():
    start = date(2024, 1, 1)
    rows = [
        bal_row("A1", "P1", start, 10000),
        bal_row("A1", "P1", start + timedelta(days=61), 12000),
        bal_row("A1", "P1", start + timedelta(days=70), 14000),
    ]
    result = detect(pd.DataFrame(rows), CFG, "run1")
    result = apply_cooldown(result, CFG)
    statuses = result[result["event_date"] > (start + timedelta(days=65)).isoformat()]["status"].tolist()
    assert "suppressed_cooldown" in statuses


def test_no_future_leakage():
    """A single row with a much larger amount than nothing-prior must not
    be detected -- there is no prior baseline to compare against."""
    start = date(2024, 1, 1)
    result = detect(pd.DataFrame([bal_row("A1", "P1", start, 1_000_000)]), CFG, "run1")
    assert list(result["status"]) == ["insufficient_evidence"]


def test_missing_columns_raises():
    with pytest.raises(ValueError):
        detect(pd.DataFrame([{"foo": 1}]), CFG, "run1")
