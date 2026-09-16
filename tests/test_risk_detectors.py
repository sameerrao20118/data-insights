"""
Tests for detection_engine/rating_downgrade.py and pd_migration.py.
Hand-built frames, not generator output.

The decisive tests are the as-at ones: docs/decision_record.md's Phase 4
gate is "rating_downgrade demonstrably reads history, not current state",
and pd_migration must never invert direction (an improvement is a growth
signal, not a risk one).
"""

from datetime import date

import pandas as pd
import pytest

from detection_engine import pd_migration, rating_downgrade

RD = rating_downgrade.DetectorConfig(lookback_days=180, min_notches=2, cooldown_days=90, rule_version="test.v1")
PM = pd_migration.DetectorConfig(lookback_days=180, min_relative_change=0.5, min_abs_change=0.005,
                                  emit_improvements=False, cooldown_days=90, rule_version="test.v1")
PM_IMPROVE = pd_migration.DetectorConfig(lookback_days=180, min_relative_change=0.5, min_abs_change=0.005,
                                          emit_improvements=True, cooldown_days=90, rule_version="test.v1")


def party(prty_id, start, end, grd_cd, grd_val):
    return {"PRTY_ID": prty_id, "EFFECTIVE_START_DT": start, "EFFECTIVE_END_DT": end or "",
            "RSK_GRD_CD": grd_cd, "RSK_GRD_VAL": grd_val}


def metric(prty_id, start, end, val, typ="PD_1Y"):
    return {"PRTY_ID": prty_id, "EFFECTIVE_START_DT": start, "EFFECTIVE_END_DT": end or "",
            "PRTY_MTR_TYP_CD": typ, "PRTY_MTR_VAL": val}


# ---------------------------------------------------------------- rating_downgrade

def test_genuine_downgrade_detected():
    """Grade worsened from 3 to 6 (3 notches, on the 1=best..10=worst scale)
    -- above min_notches, must fire."""
    history = pd.DataFrame([
        party("P1", date(2023, 1, 1), date(2024, 6, 1), "B", 3),
        party("P1", date(2024, 6, 1), None, "CCC", 6),
    ])
    result = rating_downgrade.detect(history, RD, "run1", date(2024, 6, 15))
    assert len(result) == 1
    assert result.iloc[0]["prior_grade_val"] == 3
    assert result.iloc[0]["current_grade_val"] == 6
    assert result.iloc[0]["notches"] == 3


def test_as_at_before_the_downgrade_not_detected():
    """The exact same history, evaluated as-at a date BEFORE the downgrade
    took effect, must not detect it -- proves the detector reads the grade
    valid at as_of, not the latest row in the table."""
    history = pd.DataFrame([
        party("P1", date(2023, 1, 1), date(2024, 6, 1), "B", 3),
        party("P1", date(2024, 6, 1), None, "CCC", 6),
    ])
    result = rating_downgrade.detect(history, RD, "run1", date(2024, 3, 1))
    assert result.empty


def test_upgrade_not_a_downgrade():
    """Grade improved (value decreased) -- must not fire."""
    history = pd.DataFrame([
        party("P1", date(2023, 1, 1), date(2024, 6, 1), "CCC", 6),
        party("P1", date(2024, 6, 1), None, "B", 3),
    ])
    result = rating_downgrade.detect(history, RD, "run1", date(2024, 6, 15))
    assert result.empty


def test_small_move_below_min_notches_not_detected():
    """Only 1 notch of movement -- below min_notches=2, must not fire."""
    history = pd.DataFrame([
        party("P1", date(2023, 1, 1), date(2024, 6, 1), "B", 3),
        party("P1", date(2024, 6, 1), None, "B-", 4),
    ])
    result = rating_downgrade.detect(history, RD, "run1", date(2024, 6, 15))
    assert result.empty


def test_no_prior_version_not_evaluable():
    """Only one grade ever recorded -- no history to have been downgraded
    from, so no detection, not a false 'downgraded from nothing'."""
    history = pd.DataFrame([party("P1", date(2023, 1, 1), None, "B", 3)])
    result = rating_downgrade.detect(history, RD, "run1", date(2024, 6, 15))
    assert result.empty


def test_cooldown_suppresses_repeat_alert():
    history = pd.DataFrame([
        party("P1", date(2023, 1, 1), date(2024, 6, 1), "B", 3),
        party("P1", date(2024, 6, 1), None, "CCC", 6),
    ])
    r1 = rating_downgrade.detect(history, RD, "run1", date(2024, 6, 15))
    r2 = rating_downgrade.detect(history, RD, "run2", date(2024, 6, 20))
    combined = pd.concat([r1, r2], ignore_index=True)
    result = rating_downgrade.apply_cooldown(combined, RD)
    assert list(result["status"]) == ["detected", "suppressed_cooldown"]


def test_rating_downgrade_missing_columns_raises():
    with pytest.raises(ValueError):
        rating_downgrade.detect(pd.DataFrame([{"foo": 1}]), RD, "run1", date(2023, 1, 1))


def test_rating_downgrade_to_signal_direction_is_decrease():
    """Credit quality got worse -- Signal.direction must be 'decrease'
    (a risk signal), regardless of RSK_GRD_VAL going up."""
    history = pd.DataFrame([
        party("P1", date(2023, 1, 1), date(2024, 6, 1), "B", 3),
        party("P1", date(2024, 6, 1), None, "CCC", 6),
    ])
    result = rating_downgrade.detect(history, RD, "run1", date(2024, 6, 15))
    sig = rating_downgrade.to_signal(result.iloc[0])
    assert sig.direction == "decrease"
    assert sig.domain == "risk"


# ---------------------------------------------------------------- pd_migration

def test_pd_deterioration_detected_as_increase():
    """PD roughly doubled -- above both min_relative_change and
    min_abs_change, must fire with direction 'increase' (a risk signal)."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.02),
        metric("P1", date(2024, 6, 1), None, 0.05),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    assert len(result) == 1
    assert result.iloc[0]["direction"] == "increase"
    assert result.iloc[0]["prior_pd_pct"] == 0.02
    assert result.iloc[0]["current_pd_pct"] == 0.05


def test_pd_improvement_not_emitted_by_default():
    """PD improved (dropped) -- with emit_improvements=False (the default),
    this must NOT be emitted: an improvement is a growth signal, and the
    correlation layer isn't yet direction-aware, so letting it through
    would wrongly suppress revenue recommendations (decision_record.md's
    inversion warning)."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.05),
        metric("P1", date(2024, 6, 1), None, 0.02),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    assert result.empty


def test_pd_improvement_emitted_when_enabled_with_decrease_direction():
    """Same history, with emit_improvements=True -- must fire, and
    direction must be 'decrease', never inverted to 'increase'."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.05),
        metric("P1", date(2024, 6, 1), None, 0.02),
    ])
    result = pd_migration.detect(history, PM_IMPROVE, "run1", date(2024, 6, 15))
    assert len(result) == 1
    assert result.iloc[0]["direction"] == "decrease"


def test_as_at_before_the_migration_not_detected():
    """Evaluated as-at a date before the PD moved -- must not detect it."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.02),
        metric("P1", date(2024, 6, 1), None, 0.05),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 3, 1))
    assert result.empty


def test_small_relative_change_below_threshold_not_detected():
    """PD moved from 0.02 to 0.025 -- 25% relative change, below
    min_relative_change=0.5, must not fire even though it's a deterioration."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.02),
        metric("P1", date(2024, 6, 1), None, 0.025),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    assert result.empty


def test_small_absolute_change_below_threshold_not_detected():
    """PD moved from 0.001 to 0.002 -- doubled (100% relative change) but
    the absolute move (0.001) is below min_abs_change=0.005, must not fire."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.001),
        metric("P1", date(2024, 6, 1), None, 0.002),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    assert result.empty


def test_zero_prior_pd_not_evaluable():
    """Prior PD of 0 makes relative change undefined -- must be skipped,
    not treated as an infinite migration."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.0),
        metric("P1", date(2024, 6, 1), None, 0.05),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    assert result.empty


def test_no_prior_pd_not_evaluable():
    """Only one PD value ever recorded -- no history to migrate from."""
    history = pd.DataFrame([metric("P1", date(2023, 1, 1), None, 0.05)])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    assert result.empty


def test_other_metric_types_ignored():
    """Non-PD_1Y metric rows for the same party must not be picked up."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.02, typ="OTHER_METRIC"),
        metric("P1", date(2024, 6, 1), None, 0.05, typ="OTHER_METRIC"),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    assert result.empty


def test_pd_migration_cooldown_suppresses_repeat_alert():
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.02),
        metric("P1", date(2024, 6, 1), None, 0.05),
    ])
    r1 = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    r2 = pd_migration.detect(history, PM, "run2", date(2024, 6, 20))
    combined = pd.concat([r1, r2], ignore_index=True)
    result = pd_migration.apply_cooldown(combined, PM)
    assert list(result["status"]) == ["detected", "suppressed_cooldown"]


def test_pd_migration_missing_columns_raises():
    with pytest.raises(ValueError):
        pd_migration.detect(pd.DataFrame([{"foo": 1}]), PM, "run1", date(2023, 1, 1))


def test_pd_migration_to_signal_direction_matches_row():
    """Signal.direction must come straight from the row, never re-derived
    -- the one place an inversion bug would otherwise hide."""
    history = pd.DataFrame([
        metric("P1", date(2023, 1, 1), date(2024, 6, 1), 0.02),
        metric("P1", date(2024, 6, 1), None, 0.05),
    ])
    result = pd_migration.detect(history, PM, "run1", date(2024, 6, 15))
    sig = pd_migration.to_signal(result.iloc[0])
    assert sig.direction == "increase"
    assert sig.domain == "risk"
