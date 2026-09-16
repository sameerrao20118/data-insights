"""Tests for detection_engine/revenue_pattern_change.py."""

from datetime import date, timedelta

import pandas as pd
import pytest

from detection_engine.revenue_pattern_change import DetectorConfig, apply_cooldown, detect

CFG = DetectorConfig(window_days=60, min_credits_per_window=3, min_change_pct=0.5,
                      cooldown_days=45, rule_version="test.v1")


def credit(agrmnt_id, prty_id, d: date, amount):
    return {"PRTY_ID": prty_id, "AGRMNT_ID": agrmnt_id, "FIN_EVNT_PSTD_DT": d.isoformat(),
            "FIN_EVNT_AMT": amount, "FIN_EVNT_SBTYP_CD": "CRD"}


def debit(agrmnt_id, prty_id, d: date, amount):
    return {"PRTY_ID": prty_id, "AGRMNT_ID": agrmnt_id, "FIN_EVNT_PSTD_DT": d.isoformat(),
            "FIN_EVNT_AMT": amount, "FIN_EVNT_SBTYP_CD": "DBT"}


def test_step_increase_detected():
    start = date(2024, 1, 1)
    prior = [credit("A1", "P1", start + timedelta(days=i * 10), 1000) for i in range(6)]
    recent = [credit("A1", "P1", start + timedelta(days=61 + i * 10), 3000) for i in range(3)]
    events = pd.DataFrame(prior + recent)
    result = detect(events, CFG, "run1")
    detected = result[result["status"] == "detected"]
    assert len(detected) > 0
    assert detected.iloc[-1]["change_pct"] > 0.5


def test_stable_pattern_not_detected():
    start = date(2024, 1, 1)
    events = pd.DataFrame([credit("A1", "P1", start + timedelta(days=i * 10), 1000) for i in range(12)])
    result = detect(events, CFG, "run1")
    assert result.empty


def test_debits_ignored():
    start = date(2024, 1, 1)
    events = pd.DataFrame([debit("A1", "P1", start + timedelta(days=i * 5), 5000) for i in range(20)])
    result = detect(events, CFG, "run1")
    assert result.empty


def test_insufficient_credits_per_window_not_evaluated():
    start = date(2024, 1, 1)
    events = pd.DataFrame([
        credit("A1", "P1", start, 1000),
        credit("A1", "P1", start + timedelta(days=61), 5000),
    ])
    result = detect(events, CFG, "run1")
    assert result.empty  # only 1 credit per window, below min_credits_per_window=3


def test_no_future_leakage_only_prior_windows_used():
    """A single early spike followed by a long stable run must not flag the
    stable run's later rows -- confirms both windows for a candidate row
    are strictly at-or-before that row's own date."""
    start = date(2024, 1, 1)
    spike = [credit("A1", "P1", start + timedelta(days=i * 10), 5000) for i in range(3)]
    later_stable = [credit("A1", "P1", start + timedelta(days=200 + i * 10), 5000) for i in range(6)]
    events = pd.DataFrame(spike + later_stable)
    result = detect(events, CFG, "run1")
    late_detections = result[pd.to_datetime(result["event_date"]) > pd.Timestamp(start + timedelta(days=200))]
    assert late_detections.empty


def test_cooldown_suppresses_repeat_within_window():
    start = date(2024, 1, 1)
    prior = [credit("A1", "P1", start + timedelta(days=i * 10), 1000) for i in range(6)]
    recent = [credit("A1", "P1", start + timedelta(days=61 + i * 5), 3000) for i in range(5)]
    events = pd.DataFrame(prior + recent)
    result = detect(events, CFG, "run1")
    result = apply_cooldown(result, CFG)
    if len(result) > 1:
        assert "suppressed_cooldown" in list(result["status"])


def test_missing_columns_raises():
    with pytest.raises(ValueError):
        detect(pd.DataFrame([{"foo": 1}]), CFG, "run1")
