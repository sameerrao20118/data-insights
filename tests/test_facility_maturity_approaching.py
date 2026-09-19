"""Tests for detection_engine/facility_maturity_approaching.py."""

from datetime import date, timedelta

import pandas as pd
import pytest

from detection_engine.facility_maturity_approaching import DetectorConfig, apply_cooldown, detect

CFG = DetectorConfig(horizon_days=90, cooldown_days=30, rule_version="test.v1")


def agr(agrmnt_id, prty_id, close_dt):
    return {"party_id": prty_id, "account_id": agrmnt_id,
            "close_date": close_dt.isoformat() if close_dt else ""}


def test_within_horizon_detected():
    as_of = date(2024, 1, 1)
    result = detect(pd.DataFrame([agr("A1", "P1", as_of + timedelta(days=30))]), CFG, "run1", as_of)
    assert len(result) == 1
    assert result.iloc[0]["days_to_close"] == 30


def test_beyond_horizon_not_detected():
    as_of = date(2024, 1, 1)
    result = detect(pd.DataFrame([agr("A1", "P1", as_of + timedelta(days=200))]), CFG, "run1", as_of)
    assert result.empty


def test_already_past_close_date_not_detected():
    as_of = date(2024, 6, 1)
    result = detect(pd.DataFrame([agr("A1", "P1", date(2024, 1, 1))]), CFG, "run1", as_of)
    assert result.empty


def test_no_close_date_excluded_not_error():
    as_of = date(2024, 1, 1)
    result = detect(pd.DataFrame([agr("A1", "P1", None)]), CFG, "run1", as_of)
    assert result.empty


def test_cooldown_suppresses_repeat_alert():
    as_of = date(2024, 1, 1)
    close = as_of + timedelta(days=30)
    r1 = detect(pd.DataFrame([agr("A1", "P1", close)]), CFG, "run1", as_of)
    r2 = detect(pd.DataFrame([agr("A1", "P1", close)]), CFG, "run2", as_of + timedelta(days=5))
    combined = pd.concat([r1, r2], ignore_index=True)
    result = apply_cooldown(combined, CFG)
    assert list(result["status"]) == ["detected", "suppressed_cooldown"]


def test_missing_columns_raises():
    with pytest.raises(ValueError):
        detect(pd.DataFrame([{"foo": 1}]), CFG, "run1", date(2024, 1, 1))


def test_orig_limit_carried_when_present_and_none_when_absent():
    """Carried so a renewal can be sized at the current limit; optional so
    callers without a limit column keep working."""
    as_of = date(2024, 1, 1)
    with_limit = pd.DataFrame([{**agr("A1", "P1", as_of + timedelta(days=30)), "original_limit": 250_000.0}])
    assert detect(with_limit, CFG, "run1", as_of).iloc[0]["orig_limit"] == 250_000.0
    without = detect(pd.DataFrame([agr("A1", "P1", as_of + timedelta(days=30))]), CFG, "run1", as_of)
    assert without.iloc[0]["orig_limit"] is None
