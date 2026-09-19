"""
R5: SqlSource is proven without credentials -- the same contract and the
same CSV fixture, read through OfflineLocalSource (DuckDB over CSV) and
through SqlSource against a DuckDB database loaded from those CSVs, must
produce identical frames for read_entity, aggregate and changed_since.
Real Postgres / SQL Server / Snowflake / Athena stay NOT RUN and fail
closed with a plain message.
"""

from __future__ import annotations

import os
from datetime import date

import duckdb
import pandas as pd
import pytest

from datainsights.config import Profile
from datainsights.runtime import _build_source
from datainsights.sources.base import DataSourceError
from datainsights.sources.offline_local import OfflineLocalSource
from datainsights.sources.sql_source import DIALECTS, AthenaSource, SqlSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBA_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm_sba")
SBA_CONTRACT = os.path.join(REPO_ROOT, "config", "entities_sba.yaml")

pytestmark = pytest.mark.skipif(not os.path.isdir(SBA_DIR), reason="SBA data not generated")


@pytest.fixture(scope="module")
def sources():
    local = OfflineLocalSource(SBA_DIR, SBA_CONTRACT)
    con = duckdb.connect(database=":memory:")
    for entity, spec in local._contract["entities"].items():
        path = os.path.join(SBA_DIR, f"{spec['physical_table']}.csv")
        con.execute(f"CREATE TABLE \"{spec['physical_table']}\" AS SELECT * FROM read_csv_auto('{path}')")
    sql = SqlSource("duckdb", SBA_CONTRACT, connection_factory=lambda: con, default_schema=None)
    return local, sql


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.columns:
        if "date" in c.lower() or c.lower().endswith("_dt"):
            df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d")
    return df.sort_values(list(df.columns)).reset_index(drop=True)


def test_read_entity_is_identical_for_a_bounded_window(sources):
    local, sql = sources
    kw = dict(start_date=date(2025, 3, 1), end_date=date(2025, 4, 30))
    a, _ = local.read_entity("balances", **kw)
    b, prov = sql.read_entity("balances", **kw)
    assert len(a) > 0
    pd.testing.assert_frame_equal(_norm(a), _norm(b[a.columns]))
    assert prov.backend == "sql:duckdb" and prov.row_count == len(a)


@pytest.mark.parametrize("agg", ["mean", "sum", "count", "last", "first"])
def test_aggregate_pushdown_is_identical(sources, agg):
    local, sql = sources
    kw = dict(group_col="account_id", value_col="balance", date_col="observed_at", agg=agg,
              as_of=date(2025, 6, 30), window_days=60)
    a = local.aggregate("balances", **kw)
    b = sql.aggregate("balances", **kw)
    a, b = a.sort_values("account_id").reset_index(drop=True), b.sort_values("account_id").reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b.astype(a.dtypes.to_dict()), check_dtype=False)


def test_changed_since_returns_only_rows_after_the_watermark(sources):
    local, sql = sources
    assert sql.capabilities().supports_change_detection is True
    changed = sql.changed_since("balances", date(2025, 6, 1), as_of=date(2025, 6, 30))
    full, _ = local.read_entity("balances", start_date=date(2025, 6, 2), end_date=date(2025, 6, 30))
    assert len(changed) == len(full) > 0
    with pytest.raises(DataSourceError):
        sql.changed_since("parties", date(2025, 6, 1))  # no event-time field declared


def test_unbounded_read_is_refused_and_row_limit_enforced(sources):
    _, sql = sources
    with pytest.raises(DataSourceError, match="Unbounded"):
        sql.read_entity("balances")
    small = SqlSource("duckdb", SBA_CONTRACT, connection_factory=sql._connection_factory, statement_row_limit=10)
    with pytest.raises(DataSourceError, match="more than 10"):
        small.read_entity("balances", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))


@pytest.mark.parametrize("dialect", [d for d in DIALECTS if d != "duckdb"])
def test_every_real_dialect_constructs_without_connecting_and_fails_closed(dialect, monkeypatch):
    for k in list(os.environ):
        if k.startswith("POC_"):
            monkeypatch.delenv(k)
    src = SqlSource(dialect, SBA_CONTRACT, connection_ref="poc", default_schema="PUBLIC")
    assert src.capabilities().backend == f"sql:{dialect}"
    with pytest.raises(EnvironmentError, match="NOT RUN"):
        src.read_entity("balances", start_date=date(2025, 1, 1), end_date=date(2025, 1, 2))


def test_profile_builds_the_real_adapters_lazily():
    base = dict(config_version=1, profile="t", runtime={"target": "local"},
                analytics={"backend": "duckdb_local"},
                llm={"provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "x"},
                state={"backend": "sqlite", "path": "var/x.db"}, output={"backend": "local", "path": "var/out"},
                monitor={"enabled": False}, cost={})
    pg = Profile.model_validate({**base, "source": {"backend": "postgres", "entity_map_ref": "config/entities_sba.yaml",
                                                     "connection_ref": "poc", "default_schema": "public",
                                                     "cost_policy": "no_cost_until_deployed_not_run"}})
    assert isinstance(_build_source(pg), SqlSource)
    ath = Profile.model_validate({**base, "source": {"backend": "glue_athena", "entity_map_ref": "config/entities_sba.yaml",
                                                      "glue_database": "poc_db", "athena_workgroup": "primary",
                                                      "cost_policy": "no_cost_until_deployed_not_run"}})
    a = _build_source(ath)
    assert isinstance(a, AthenaSource) and a.default_schema == "poc_db" and a.athena_workgroup == "primary"
