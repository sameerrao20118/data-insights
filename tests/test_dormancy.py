"""Tests for detection_engine/dormancy.py."""

from datetime import date, timedelta

import pandas as pd
import pytest

from detection_engine.dormancy import DetectorConfig, apply_cooldown, detect

CFG = DetectorConfig(dormancy_days=60, cooldown_days=45, rule_version="test.v1")


def ev(agrmnt_id, prty_id, d: date):
    return {"PRTY_ID": prty_id, "AGRMNT_ID": agrmnt_id, "FIN_EVNT_PSTD_DT": d.isoformat()}


def test_never_active_account_not_flagged():
    result = detect(pd.DataFrame(columns=["PRTY_ID", "AGRMNT_ID", "FIN_EVNT_PSTD_DT"]),
                     ["A1"], CFG, "run1", date(2024, 6, 1))
    assert result.empty


def test_recently_active_not_flagged():
    events = pd.DataFrame([ev("A1", "P1", date(2024, 5, 20))])
    result = detect(events, ["A1"], CFG, "run1", date(2024, 6, 1))
    assert result.empty


def test_dormant_beyond_threshold_flagged():
    events = pd.DataFrame([ev("A1", "P1", date(2024, 1, 1))])
    as_of = date(2024, 1, 1) + timedelta(days=61)
    result = detect(events, ["A1"], CFG, "run1", as_of)
    assert len(result) == 1
    assert result.iloc[0]["status"] == "detected"
    assert result.iloc[0]["days_since_last_event"] == 61


def test_exactly_at_threshold_flagged():
    events = pd.DataFrame([ev("A1", "P1", date(2024, 1, 1))])
    as_of = date(2024, 1, 1) + timedelta(days=60)
    result = detect(events, ["A1"], CFG, "run1", as_of)
    assert len(result) == 1


def test_agreement_not_in_scope_ignored():
    events = pd.DataFrame([ev("A1", "P1", date(2024, 1, 1))])
    result = detect(events, ["A2"], CFG, "run1", date(2024, 6, 1))
    assert result.empty


def test_cooldown_suppresses_repeat_alert_on_still_dormant_account():
    events = pd.DataFrame([ev("A1", "P1", date(2024, 1, 1))])
    r1 = detect(events, ["A1"], CFG, "run1", date(2024, 1, 1) + timedelta(days=61))
    r2 = detect(events, ["A1"], CFG, "run2", date(2024, 1, 1) + timedelta(days=70))
    combined = pd.concat([r1, r2], ignore_index=True)
    result = apply_cooldown(combined, CFG)
    assert list(result["status"]) == ["detected", "suppressed_cooldown"]


def test_missing_columns_raises():
    with pytest.raises(ValueError):
        detect(pd.DataFrame([{"foo": 1}]), ["A1"], CFG, "run1", date(2024, 1, 1))
