"""Tests for detection_engine/fixed_rate_expiry.py."""

from datetime import date, timedelta

import pandas as pd
import pytest

from detection_engine.fixed_rate_expiry import DetectorConfig, apply_cooldown, detect

CFG = DetectorConfig(horizon_days=90, cooldown_days=30, rule_version="test.v1")


def mort(agrmnt_id, prty_id, fxd_end):
    return {"PRTY_ID": prty_id, "AGRMNT_ID": agrmnt_id,
            "MORT_FXED_RT_END_DT": fxd_end.isoformat() if fxd_end else ""}


def test_within_horizon_detected():
    as_of = date(2024, 1, 1)
    result = detect(pd.DataFrame([mort("A1", "P1", as_of + timedelta(days=45))]), CFG, "run1", as_of)
    assert len(result) == 1
    assert result.iloc[0]["days_to_expiry"] == 45


def test_beyond_horizon_not_detected():
    as_of = date(2024, 1, 1)
    result = detect(pd.DataFrame([mort("A1", "P1", as_of + timedelta(days=400))]), CFG, "run1", as_of)
    assert result.empty


def test_no_fixed_rate_excluded():
    as_of = date(2024, 1, 1)
    result = detect(pd.DataFrame([mort("A1", "P1", None)]), CFG, "run1", as_of)
    assert result.empty


def test_already_expired_not_detected():
    as_of = date(2024, 6, 1)
    result = detect(pd.DataFrame([mort("A1", "P1", date(2024, 1, 1))]), CFG, "run1", as_of)
    assert result.empty


def test_cooldown_suppresses_repeat_alert():
    as_of = date(2024, 1, 1)
    expiry = as_of + timedelta(days=30)
    r1 = detect(pd.DataFrame([mort("A1", "P1", expiry)]), CFG, "run1", as_of)
    r2 = detect(pd.DataFrame([mort("A1", "P1", expiry)]), CFG, "run2", as_of + timedelta(days=5))
    combined = pd.concat([r1, r2], ignore_index=True)
    result = apply_cooldown(combined, CFG)
    assert list(result["status"]) == ["detected", "suppressed_cooldown"]


def test_missing_columns_raises():
    with pytest.raises(ValueError):
        detect(pd.DataFrame([{"foo": 1}]), CFG, "run1", date(2024, 1, 1))
