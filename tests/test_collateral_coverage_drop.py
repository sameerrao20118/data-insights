"""
Tests for detection_engine/collateral_coverage_drop.py -- this pass's
bi-temporal proof detector (see config/entities_fdm.yaml's note). The
as-at/history test below is the one that matters most: it must fail if
the detector were changed to look at only the latest row.
"""

from datetime import date

import pandas as pd
import pytest

from detection_engine.collateral_coverage_drop import DetectorConfig, apply_cooldown, detect

CFG = DetectorConfig(coverage_threshold_pct=1.0, cooldown_days=30, rule_version="test.v1")


def cov(agrmnt_id, cltrl_item_id, prty_id, eff_start: date, orig_lim, cltrl_val):
    return {"party_id": prty_id, "account_id": agrmnt_id, "collateral_id": cltrl_item_id,
            "original_limit": orig_lim, "value": cltrl_val,
            "valid_from": eff_start.isoformat()}


def test_genuine_drop_detected():
    """Coverage was 1.5x (adequate) at open, dropped to 0.7x (inadequate)
    later -- this is the actual failure mode as-at correctness must catch:
    a detector reading only 'latest value' would still catch this one, but
    the next test proves this detector is actually using history, not
    just happening to agree with it here."""
    history = pd.DataFrame([
        cov("A1", "C1", "P1", date(2024, 1, 1), 100_000, 150_000),
        cov("A1", "C1", "P1", date(2024, 6, 1), 100_000, 70_000),
    ])
    result = detect(history, CFG, "run1", date(2024, 6, 15))
    assert len(result) == 1
    assert result.iloc[0]["prior_max_coverage_pct"] == 1.5
    assert result.iloc[0]["current_coverage_pct"] == 0.7


def test_as_at_before_the_drop_not_detected():
    """The exact same history, evaluated as-at a date BEFORE the drop
    happened, must not detect it -- proves the detector respects as_at
    and doesn't leak the future drop backward."""
    history = pd.DataFrame([
        cov("A1", "C1", "P1", date(2024, 1, 1), 100_000, 150_000),
        cov("A1", "C1", "P1", date(2024, 6, 1), 100_000, 70_000),
    ])
    result = detect(history, CFG, "run1", date(2024, 3, 1))
    assert result.empty


def test_always_thin_coverage_not_a_drop():
    """Coverage was always below threshold -- never adequately covered,
    so this is a standing risk fact, not a 'drop', and must not fire."""
    history = pd.DataFrame([
        cov("A1", "C1", "P1", date(2024, 1, 1), 100_000, 60_000),
        cov("A1", "C1", "P1", date(2024, 6, 1), 100_000, 55_000),
    ])
    result = detect(history, CFG, "run1", date(2024, 6, 15))
    assert result.empty


def test_single_version_no_history_not_evaluable():
    """Only one valuation ever recorded -- can't distinguish 'always thin'
    from 'just dropped', so no detection, not a false positive."""
    history = pd.DataFrame([cov("A1", "C1", "P1", date(2024, 1, 1), 100_000, 50_000)])
    result = detect(history, CFG, "run1", date(2024, 6, 15))
    assert result.empty


def test_coverage_stays_adequate_not_detected():
    history = pd.DataFrame([
        cov("A1", "C1", "P1", date(2024, 1, 1), 100_000, 150_000),
        cov("A1", "C1", "P1", date(2024, 6, 1), 100_000, 120_000),
    ])
    result = detect(history, CFG, "run1", date(2024, 6, 15))
    assert result.empty


def test_cooldown_suppresses_repeat_alert():
    history = pd.DataFrame([
        cov("A1", "C1", "P1", date(2024, 1, 1), 100_000, 150_000),
        cov("A1", "C1", "P1", date(2024, 6, 1), 100_000, 70_000),
    ])
    r1 = detect(history, CFG, "run1", date(2024, 6, 15))
    r2 = detect(history, CFG, "run2", date(2024, 6, 20))
    combined = pd.concat([r1, r2], ignore_index=True)
    result = apply_cooldown(combined, CFG)
    assert list(result["status"]) == ["detected", "suppressed_cooldown"]


def test_missing_columns_raises():
    with pytest.raises(ValueError):
        detect(pd.DataFrame([{"foo": 1}]), CFG, "run1", date(2024, 1, 1))
