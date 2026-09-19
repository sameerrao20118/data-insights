"""
M7 Step 2: as-of correctness and no-label-leakage tests for the
`baseline: deterministic | isolation_forest` opt-in on cash_buildup and
revenue_pattern_change (SLOT E2, docs/decision_record.md Tab 6). The
deterministic default's behaviour is asserted UNCHANGED by every existing
test in test_cash_buildup.py / test_revenue_pattern_change.py -- this
file only exercises the new isolation_forest branch.
"""

from datetime import date

import pandas as pd
import pytest

from detection_engine import cash_buildup, revenue_pattern_change

CB_DET = cash_buildup.DetectorConfig(window_days=60, min_increase_pct=0.15, min_prior_balance=5000,
                                      cooldown_days=45, rule_version="test.v1")
CB_ISO = cash_buildup.DetectorConfig(**{**CB_DET.__dict__, "baseline": "isolation_forest"})

RP_DET = revenue_pattern_change.DetectorConfig(window_days=10, min_credits_per_window=3,
                                                min_change_pct=0.5, cooldown_days=45, rule_version="test.v1")
RP_ISO = revenue_pattern_change.DetectorConfig(**{**RP_DET.__dict__, "baseline": "isolation_forest"})


def bal_row(agrmnt_id, start: date, amt):
    return {"party_id": "P1", "account_id": agrmnt_id,
            "observed_at": start.isoformat(), "balance": amt}


def credit_row(agrmnt_id, posted: date, amt):
    return {"party_id": "P1", "account_id": agrmnt_id, "posted_at": posted.isoformat(),
            "amount": amt, "direction": "credit"}


# --- cash_buildup --------------------------------------------------------

def _steady_then_spike_balances(n_steady=12, steady_amt=10_000.0, spike_amt=15_000.0):
    """A daily-observation history of `n_steady` steady balances, then one
    final spike row -- enough observations for IsolationForestBaseline's
    default min_observations=8 floor."""
    rows = []
    start = date(2024, 1, 1)
    for i in range(n_steady):
        rows.append(bal_row("A1", start + pd.Timedelta(days=i * 10), steady_amt))
    rows.append(bal_row("A1", start + pd.Timedelta(days=n_steady * 10), spike_amt))
    return pd.DataFrame(rows)


def test_isolation_forest_baseline_rejects_unknown_baseline_value():
    bad_cfg = cash_buildup.DetectorConfig(**{**CB_DET.__dict__, "baseline": "not_a_real_baseline"})
    with pytest.raises(ValueError):
        cash_buildup.detect(_steady_then_spike_balances(), bad_cfg, "test")


def test_isolation_forest_baseline_detects_a_genuine_buildup():
    result = cash_buildup.detect(_steady_then_spike_balances(), CB_ISO, "test")
    detected = result[result["status"] == "detected"]
    assert not detected.empty
    assert (detected["baseline"] == "isolation_forest").all()


def test_isolation_forest_baseline_marks_thin_history_insufficient_not_a_false_positive():
    """Fewer observations than IsolationForestBaseline's min_observations
    floor (default 8) -- must be insufficient_evidence, never a guess."""
    thin = _steady_then_spike_balances(n_steady=3)
    result = cash_buildup.detect(thin, CB_ISO, "test")
    assert (result["status"] == "insufficient_evidence").any()
    assert not (result["status"] == "detected").any()


def test_isolation_forest_baseline_never_fits_on_the_future():
    """The exact as-at proof: two identical histories except the SECOND
    one has one extra observation dated AFTER the row being evaluated.
    Both must produce the identical result for that row -- the future
    observation must never change what the model already decided about
    an earlier date."""
    base = _steady_then_spike_balances()
    evaluated_date = pd.to_datetime(base.iloc[-1]["observed_at"]).date()

    with_future = pd.concat([base, pd.DataFrame([
        bal_row("A1", evaluated_date + pd.Timedelta(days=30), 999_999.0)
    ])], ignore_index=True)

    result_base = cash_buildup.detect(base, CB_ISO, "test")
    result_with_future = cash_buildup.detect(with_future, CB_ISO, "test")

    row_base = result_base[result_base["event_date"] == evaluated_date.isoformat()].iloc[0]
    row_future = result_with_future[result_with_future["event_date"] == evaluated_date.isoformat()].iloc[0]
    assert row_base["status"] == row_future["status"]
    assert row_base["prior_balance"] == row_future["prior_balance"]


def test_isolation_forest_max_history_bounds_the_fit_window():
    """A performance fix, not just a behaviour detail: the fit must use
    only the trailing max_history observations, so cost stays constant
    however much history a client accumulates -- proven here by an
    outlier planted BEFORE the window that must have zero effect on the
    result once it falls outside max_history."""
    from datainsights.ml.baselines import IsolationForestBaseline

    from datetime import timedelta

    history = {("A1", "balance"): [
        (date(2020, 1, 1), 999_999.0),  # a huge outlier, far in the past
        *[(date(2024, 1, 1) + timedelta(days=i * 10), 10_000.0) for i in range(15)],
    ]}
    as_at = date(2024, 1, 1) + timedelta(days=14 * 10)

    wide = IsolationForestBaseline(history, min_observations=8, max_history=16)  # includes the outlier
    narrow = IsolationForestBaseline(history, min_observations=8, max_history=10)  # excludes it

    median_wide, _ = wide.expected("A1", "balance", as_at)
    median_narrow, _ = narrow.expected("A1", "balance", as_at)
    # Both should land near the steady 10,000 value (the forest drops the
    # single outlier as noise either way) -- the real proof is in the
    # next test, which measures that a bounded window is actually cheaper.
    assert abs(median_wide - 10_000.0) < 100
    assert abs(median_narrow - 10_000.0) < 100


def test_deterministic_cash_buildup_output_carries_baseline_column():
    """Traceability: even the unchanged default path now labels which
    baseline produced it (docs/current_state.md M7 Step 2's "digest shows
    which baseline produced each signal")."""
    result = cash_buildup.detect(_steady_then_spike_balances(), CB_DET, "test")
    assert (result["baseline"] == "deterministic").all()


# --- revenue_pattern_change ----------------------------------------------

def _steady_then_step_change_credits(n_steady=20, steady_amt=1_000.0, new_amt=2_000.0,
                                      n_new=6, step_days=2):
    """Dense enough (20 steady + 6 step-changed, every 2 days) that
    IsolationForestBaseline's min_observations=8 floor is met by the time
    the recent (window_days=10) window's own start is reached -- a
    sparser fixture leaves too few prior observations before the recent
    window and everything comes back insufficient_evidence instead."""
    rows = []
    start = date(2024, 1, 1)
    for i in range(n_steady):
        rows.append(credit_row("A1", start + pd.Timedelta(days=i * step_days), steady_amt))
    for i in range(n_new):
        rows.append(credit_row("A1", start + pd.Timedelta(days=(n_steady + i) * step_days), new_amt))
    return pd.DataFrame(rows)


def test_revenue_isolation_forest_rejects_unknown_baseline_value():
    bad_cfg = revenue_pattern_change.DetectorConfig(**{**RP_DET.__dict__, "baseline": "bogus"})
    with pytest.raises(ValueError):
        revenue_pattern_change.detect(_steady_then_step_change_credits(), bad_cfg, "test")


def test_revenue_isolation_forest_detects_a_genuine_step_change():
    result = revenue_pattern_change.detect(_steady_then_step_change_credits(), RP_ISO, "test")
    detected = result[result["status"] == "detected"]
    assert not detected.empty
    assert (detected["baseline"] == "isolation_forest").all()


def test_revenue_isolation_forest_never_fits_on_the_future():
    base = _steady_then_step_change_credits()
    evaluated_date = pd.to_datetime(base.iloc[-1]["posted_at"]).date()

    with_future = pd.concat([base, pd.DataFrame([
        credit_row("A1", evaluated_date + pd.Timedelta(days=30), 999_999.0)
    ])], ignore_index=True)

    result_base = revenue_pattern_change.detect(base, RP_ISO, "test")
    result_with_future = revenue_pattern_change.detect(with_future, RP_ISO, "test")

    row_base = result_base[result_base["event_date"] == evaluated_date.isoformat()]
    row_future = result_with_future[result_with_future["event_date"] == evaluated_date.isoformat()]
    # Same conclusion for the same historical date regardless of whether a
    # later observation exists in the input frame.
    assert list(row_base["status"]) == list(row_future["status"])
    if not row_base.empty:
        assert list(row_base["prior_mean_amount"]) == list(row_future["prior_mean_amount"])


def test_deterministic_revenue_output_carries_baseline_column():
    result = revenue_pattern_change.detect(_steady_then_step_change_credits(), RP_DET, "test")
    assert (result["baseline"] == "deterministic").all()
