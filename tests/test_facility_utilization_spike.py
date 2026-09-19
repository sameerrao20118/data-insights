"""Tests for detection_engine/facility_utilization_spike.py."""

from datetime import date

import pandas as pd
import pytest

from detection_engine.facility_utilization_spike import DetectorConfig, apply_cooldown, detect

CFG = DetectorConfig(utilization_threshold_pct=0.85, cooldown_days=30, rule_version="test.v1")


def bal(agrmnt_id, prty_id, d: date, drawn, limit):
    return {"party_id": prty_id, "account_id": agrmnt_id, "observed_at": d.isoformat(),
            "balance": drawn, "original_limit": limit}


def test_above_threshold_detected():
    result = detect(pd.DataFrame([bal("A1", "P1", date(2024, 1, 1), 90_000, 100_000)]), CFG, "run1")
    assert len(result) == 1
    assert result.iloc[0]["status"] == "detected"
    assert abs(result.iloc[0]["utilization_pct"] - 0.9) < 1e-6


def test_below_threshold_not_detected():
    result = detect(pd.DataFrame([bal("A1", "P1", date(2024, 1, 1), 50_000, 100_000)]), CFG, "run1")
    assert result.empty


def test_exactly_at_threshold_not_detected():
    result = detect(pd.DataFrame([bal("A1", "P1", date(2024, 1, 1), 85_000, 100_000)]), CFG, "run1")
    assert result.empty  # strictly greater-than, not >=


def test_deposit_row_with_no_limit_excluded():
    result = detect(pd.DataFrame([bal("A1", "P1", date(2024, 1, 1), 50_000, 0)]), CFG, "run1")
    assert result.empty


def test_cooldown_suppresses_consecutive_daily_hits():
    rows = [bal("A1", "P1", date(2024, 1, i), 95_000, 100_000) for i in range(1, 6)]
    result = detect(pd.DataFrame(rows), CFG, "run1")
    result = apply_cooldown(result, CFG)
    assert list(result["status"]) == ["detected"] + ["suppressed_cooldown"] * 4


def test_missing_columns_raises():
    with pytest.raises(ValueError):
        detect(pd.DataFrame([{"foo": 1}]), CFG, "run1")
