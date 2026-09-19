"""
Phase 4 PIT feature layer verification (docs/generalization_plan.md):
the one property that actually matters -- a future row never changes an
earlier computed feature -- plus each aggregation's basic correctness
and the "insufficient evidence" honesty (None, never a fabricated 0).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from datainsights.features import compute


def _series(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["observed_at", "value"])


def test_a_future_row_never_changes_an_earlier_feature():
    """The actual PIT regression test: compute a feature as of a date,
    then add a row dated AFTER it with an extreme value, and confirm the
    result is byte-identical."""
    before = _series([("2026-01-01", 100.0), ("2026-01-08", 110.0), ("2026-01-15", 120.0)])
    as_of = date(2026, 1, 15)
    result_before = compute(before, as_of=as_of, date_col="observed_at", value_col="value", agg="mean")

    with_future = pd.concat([before, _series([("2026-06-01", 999_999.0)])], ignore_index=True)
    result_after = compute(with_future, as_of=as_of, date_col="observed_at", value_col="value", agg="mean")

    assert result_before == result_after == 110.0


def test_window_days_excludes_rows_outside_the_window():
    series = _series([("2026-01-01", 10.0), ("2026-03-01", 10_000.0)])
    result = compute(series, as_of=date(2026, 3, 1), date_col="observed_at", value_col="value",
                     window_days=7, agg="mean")
    assert result == 10_000.0  # the Jan row is outside a 7-day window


def test_no_rows_in_window_returns_none_not_zero():
    series = _series([("2020-01-01", 500.0)])
    result = compute(series, as_of=date(2026, 1, 1), date_col="observed_at", value_col="value",
                     window_days=7, agg="mean")
    assert result is None


def test_empty_series_returns_none():
    assert compute(pd.DataFrame(columns=["observed_at", "value"]), as_of=date(2026, 1, 1),
                   date_col="observed_at", value_col="value") is None


def test_last_returns_the_most_recent_value_by_date_not_row_order():
    # deliberately out of row order -- last() must sort by date, not trust input order
    series = _series([("2026-01-15", 30.0), ("2026-01-01", 10.0), ("2026-01-08", 20.0)])
    result = compute(series, as_of=date(2026, 1, 31), date_col="observed_at", value_col="value", agg="last")
    assert result == 30.0


def test_first_returns_the_earliest_value_by_date():
    series = _series([("2026-01-15", 30.0), ("2026-01-01", 10.0), ("2026-01-08", 20.0)])
    result = compute(series, as_of=date(2026, 1, 31), date_col="observed_at", value_col="value", agg="first")
    assert result == 10.0


def test_sum_and_count():
    series = _series([("2026-01-01", 10.0), ("2026-01-02", 20.0), ("2026-01-03", 30.0)])
    as_of = date(2026, 1, 3)
    assert compute(series, as_of=as_of, date_col="observed_at", value_col="value", agg="sum") == 60.0
    assert compute(series, as_of=as_of, date_col="observed_at", value_col="value", agg="count") == 3.0


def test_median():
    series = _series([("2026-01-01", 10.0), ("2026-01-02", 20.0), ("2026-01-03", 900.0)])
    result = compute(series, as_of=date(2026, 1, 3), date_col="observed_at", value_col="value", agg="median")
    assert result == 20.0
