"""
T6 (docs/ml_strategy_plan.md §9) -- aggregate pushdown. The golden test:
the SQL pushdown path (OfflineLocalSource.aggregate(), real DuckDB SQL)
and the pandas fallback path must return IDENTICAL values for identical
inputs -- the whole point of the optimisation is that it's invisible in
results, only in cost. No Snowflake execution here (no credentials) --
local DuckDB proof only, per the plan's own non-goal.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from datainsights.features import compute_many
from datainsights.sources.offline_local import OfflineLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBA_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm_sba")

pytestmark = pytest.mark.skipif(not os.path.isdir(SBA_DIR), reason="SBA data not generated")


class _ForcedFallback:
    """Wraps a real source but reports supports_aggregate_pushdown=False,
    so compute_many() is forced onto the pandas path even though the
    inner source actually has an aggregate() method."""

    def __init__(self, inner):
        self._inner = inner

    def capabilities(self):
        return replace(self._inner.capabilities(), supports_aggregate_pushdown=False)

    def read_entity(self, *args, **kwargs):
        return self._inner.read_entity(*args, **kwargs)


@pytest.fixture(scope="module")
def source():
    return OfflineLocalSource(SBA_DIR, os.path.join(REPO_ROOT, "config", "entities_sba.yaml"))


def test_offline_local_source_reports_pushdown_support(source):
    assert source.capabilities().supports_aggregate_pushdown is True


def test_pushdown_and_fallback_agree_on_mean(source):
    import datetime

    as_of = datetime.date(2025, 10, 4)
    pushed = compute_many(source, "balances", group_col="account_id", date_col="observed_at",
                          value_col="balance", as_of=as_of, window_days=60, agg="mean")
    fallback = compute_many(_ForcedFallback(source), "balances", group_col="account_id",
                            date_col="observed_at", value_col="balance", as_of=as_of,
                            window_days=60, agg="mean")
    assert len(pushed) == len(fallback) and len(pushed) > 0
    merged = pushed.merge(fallback, on="account_id", suffixes=("_push", "_fallback"))
    assert len(merged) == len(pushed)
    diff = (merged["balance_mean_push"] - merged["balance_mean_fallback"]).abs()
    assert diff.max() < 1e-6


@pytest.mark.parametrize("agg", ["mean", "sum", "count", "last", "first"])
def test_pushdown_and_fallback_agree_on_every_aggregation(source, agg):
    import datetime

    as_of = datetime.date(2025, 10, 4)
    pushed = compute_many(source, "balances", group_col="account_id", date_col="observed_at",
                          value_col="balance", as_of=as_of, agg=agg)
    fallback = compute_many(_ForcedFallback(source), "balances", group_col="account_id",
                            date_col="observed_at", value_col="balance", as_of=as_of, agg=agg)
    col = f"balance_{agg}"
    merged = pushed.merge(fallback, on="account_id", suffixes=("_push", "_fallback"))
    assert len(merged) == len(pushed) == len(fallback)
    diff = (merged[f"{col}_push"] - merged[f"{col}_fallback"]).abs()
    assert diff.max() < 1e-6


def test_a_source_without_aggregate_falls_back_cleanly(source):
    """A source that never implements aggregate() at all (not just
    reports False) must still work via the pandas path."""
    import datetime

    class NoAggregateAtAll:
        def __init__(self, inner):
            self._inner = inner

        def capabilities(self):
            return replace(self._inner.capabilities(), supports_aggregate_pushdown=True)  # lying, on purpose

        def read_entity(self, *args, **kwargs):
            return self._inner.read_entity(*args, **kwargs)

    as_of = datetime.date(2025, 10, 4)
    # capabilities claims pushdown support but getattr(source, "aggregate", None)
    # is None -- compute_many must not crash, must fall back.
    result = compute_many(NoAggregateAtAll(source), "balances", group_col="account_id",
                          date_col="observed_at", value_col="balance", as_of=as_of, agg="mean")
    assert len(result) > 0
