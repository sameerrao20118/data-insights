"""
SqlSource -- R5 (docs/refactor_plan.md Wave 4): ONE dialect-aware
DataSource for Postgres, SQL Server, Snowflake, Athena (and DuckDB, which
is how it is proven without credentials). The same contract file
(config/entities_<schema>.yaml) drives every backend: physical table,
domain -> schema via `domain_schema_map`, required/optional columns, and
the entity's event-time field for bounded reads.

What it implements of the DataSource surface:
  read_entity()   bounded SELECT, refuses unbounded above the row limit
  aggregate()     windowed per-entity aggregate pushed down as GROUP BY
                  (the same contract datainsights/sources/offline_local.py
                  honours, so datainsights/features.py's compute_many()
                  pushes down unchanged)
  changed_since() rows with event_time > T -- the "what changed since T"
                  primitive R11's watermark needs; supports_change_detection
                  is True exactly when the contract declares an event-time
                  field for the entity being asked about.

STATUS: proven against DuckDB in tests/test_sql_source_conformance.py --
identical frames to OfflineLocalSource on the same CSV fixture. Real
Postgres / SQL Server / Snowflake / Athena connections are NOT RUN: the
driver import and the connection happen lazily on first read, need the
<PREFIX>_* env vars, and raise a plain error naming what is missing.
Nothing here provisions, writes, or converts anything (CLAUDE.md).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import pandas as pd
import yaml

from datainsights.sources.base import BatchProvenance, DataSource, DataSourceError, SourceCapabilities

DEFAULT_STATEMENT_ROW_LIMIT = 50_000

# placeholder style and identifier quoting per dialect -- the only two
# things that actually differ for the SQL this source emits
DIALECTS = {
    "duckdb":    {"param": "?",  "quote": '"', "driver": None},
    "postgres":  {"param": "%s", "quote": '"', "driver": "psycopg"},
    "sqlserver": {"param": "?",  "quote": "[", "driver": "pyodbc"},
    "snowflake": {"param": "%s", "quote": '"', "driver": "snowflake.connector"},
    "athena":    {"param": "%s", "quote": '"', "driver": "pyathena"},
}
_AGG_SQL = {"mean": "AVG", "sum": "SUM", "count": "COUNT"}


def _q(dialect: str, ident: str) -> str:
    q = DIALECTS[dialect]["quote"]
    return f"[{ident}]" if q == "[" else f'{q}{ident}{q}'


class SqlSource(DataSource):
    def __init__(self, dialect: str, entity_map_ref: str, *, connection_ref: str | None = None,
                 domain_schema_map: dict[str, str] | None = None, default_schema: str | None = None,
                 statement_row_limit: int = DEFAULT_STATEMENT_ROW_LIMIT, query_timeout_seconds: int = 30,
                 warehouse_size: str | None = None, auto_suspend_seconds: int | None = None,
                 athena_workgroup: str | None = None, connection_factory: Optional[Callable[[], object]] = None):
        if dialect not in DIALECTS:
            raise DataSourceError(f"unknown SQL dialect {dialect!r}; one of {sorted(DIALECTS)}")
        with open(entity_map_ref) as f:
            self._contract = yaml.safe_load(f)
        self.dialect = dialect
        self.connection_ref = connection_ref
        self.domain_schema_map = dict(domain_schema_map or {})
        self.default_schema = default_schema
        self.statement_row_limit = statement_row_limit
        self.query_timeout_seconds = query_timeout_seconds
        self.warehouse_size = warehouse_size            # Snowflake session: USE WAREHOUSE sizing (NOT RUN)
        self.auto_suspend_seconds = auto_suspend_seconds  # Snowflake: ALTER WAREHOUSE ... AUTO_SUSPEND (NOT RUN)
        self.athena_workgroup = athena_workgroup
        self._connection_factory = connection_factory
        self._con = None  # lazy -- constructing must never connect
        unmapped = {spec.get("domain") for spec in self._contract.get("entities", {}).values()
                    if spec.get("domain")} - set(self.domain_schema_map)
        if unmapped and default_schema is None:
            raise DataSourceError(f"no schema mapped for contract domain(s) {sorted(unmapped)} -- add them to "
                                  f"domain_schema_map or pass default_schema; refusing to guess")

    # ---- capabilities ----------------------------------------------------
    def capabilities(self) -> SourceCapabilities:
        any_time_field = any((spec.get("time_semantics") or {}).get("event_time_field")
                             for spec in self._contract.get("entities", {}).values())
        return SourceCapabilities(backend=f"sql:{self.dialect}", supports_bounded_time_window=True,
                                  supports_change_detection=bool(any_time_field), read_only=True,
                                  supports_aggregate_pushdown=True)

    # ---- connection (lazy, fail-closed) --------------------------------
    def _connect(self):
        if self._con is not None:
            return self._con
        if self._connection_factory is not None:
            self._con = self._connection_factory()
            return self._con
        prefix = (self.connection_ref or "").upper()
        env = {k[len(prefix) + 1:].lower(): v for k, v in os.environ.items() if prefix and k.startswith(prefix + "_")}
        if not env:
            raise EnvironmentError(
                f"SQL connection {self.connection_ref!r} ({self.dialect}) is not configured -- no {prefix}_* env "
                f"vars set. NOT RUN by design: credentials never live in this repo (CLAUDE.md).")
        driver = DIALECTS[self.dialect]["driver"]
        try:
            module = __import__(driver, fromlist=["connect"])
        except ImportError as e:
            raise EnvironmentError(f"driver {driver!r} for dialect {self.dialect!r} is not installed") from e
        if self.dialect == "athena":
            self._con = module.connect(s3_staging_dir=env.get("s3_staging_dir"), region_name=env.get("region"),
                                       work_group=self.athena_workgroup, schema_name=self.default_schema)
        else:
            self._con = module.connect(**env)
        return self._con

    # ---- contract helpers ------------------------------------------------
    def _entity_spec(self, entity: str) -> dict:
        entities = self._contract.get("entities", {})
        if entity not in entities:
            raise DataSourceError(f"'{entity}' is not in the source contract ({list(entities)}); evaluator-only "
                                  f"ground truth is never contracted, by design.")
        return entities[entity]

    def _table_sql(self, spec: dict) -> str:
        schema = self.domain_schema_map.get(spec.get("domain"), self.default_schema)
        table = _q(self.dialect, spec["physical_table"])
        return f"{_q(self.dialect, schema)}.{table}" if schema else table

    def _execute(self, sql: str, params: list) -> pd.DataFrame:
        con = self._connect()
        cur = con.cursor() if hasattr(con, "cursor") else con
        cur.execute(sql, params)
        if hasattr(cur, "fetch_pandas_all"):
            return cur.fetch_pandas_all()
        if hasattr(cur, "fetchdf"):
            return cur.fetchdf()
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(rows, columns=cols)

    # ---- reads -------------------------------------------------------------
    def read_entity(self, entity: str, *, start_date: Optional[date] = None, end_date: Optional[date] = None,
                    columns: Optional[list[str]] = None, allow_unbounded: bool = False
                    ) -> tuple[pd.DataFrame, BatchProvenance]:
        spec = self._entity_spec(entity)
        required = list(spec.get("required_columns", {}).keys())
        optional = list(spec.get("optional_columns", {}).keys())
        select_cols = columns if columns else required + optional
        time_field = (spec.get("time_semantics") or {}).get("event_time_field")
        bounded = start_date is not None or end_date is not None
        if not bounded and not allow_unbounded:
            raise DataSourceError(f"Unbounded read of '{entity}' refused -- pass start_date/end_date, or "
                                  f"allow_unbounded=True if you really mean it.")
        p = DIALECTS[self.dialect]["param"]
        where, params = [], []
        if bounded and time_field:
            if start_date is not None:
                where.append(f"{_q(self.dialect, time_field)} >= {p}"); params.append(start_date.isoformat())
            if end_date is not None:
                where.append(f"{_q(self.dialect, time_field)} <= {p}"); params.append(end_date.isoformat())
        col_sql = ", ".join(_q(self.dialect, c) for c in select_cols) if select_cols else "*"
        sql = f"SELECT {col_sql} FROM {self._table_sql(spec)}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" LIMIT {self.statement_row_limit + 1}"
        df = self._execute(sql, params)
        if len(df) > self.statement_row_limit:
            raise DataSourceError(f"'{entity}' returned more than {self.statement_row_limit:,} rows -- narrow the window")
        df.columns = [str(c) for c in df.columns]
        lower = {c.lower(): c for c in df.columns}
        df = df.rename(columns={lower[c.lower()]: c for c in select_cols if c.lower() in lower})
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise DataSourceError(f"'{entity}' is missing required contract columns: {missing}")
        return df, BatchProvenance(backend=f"sql:{self.dialect}", entity=entity,
                                   extracted_as_of=datetime.now(timezone.utc).isoformat(), row_count=len(df),
                                   source_identity=f"sql:{self.dialect}:{self.connection_ref or 'injected'}")

    def aggregate(self, entity: str, *, group_col: str, value_col: str, date_col: str, agg: str, as_of: date,
                  window_days: Optional[int] = None) -> pd.DataFrame:
        """Same contract as OfflineLocalSource.aggregate(): one row per
        group_col, columns [group_col, f'{value_col}_{agg}']."""
        if agg not in ("mean", "sum", "count", "last", "first"):
            raise ValueError(f"unsupported agg: {agg!r}")
        spec = self._entity_spec(entity)
        p = DIALECTS[self.dialect]["param"]
        where = [f"{_q(self.dialect, date_col)} <= {p}"]; params = [as_of.isoformat()]
        if window_days is not None:
            where.append(f"{_q(self.dialect, date_col)} >= {p}"); params.append((as_of - timedelta(days=window_days)).isoformat())
        out_col = f"{value_col}_{agg}"
        g, v, d = _q(self.dialect, group_col), _q(self.dialect, value_col), _q(self.dialect, date_col)
        table = self._table_sql(spec)
        if agg in _AGG_SQL:
            sql = f"SELECT {g}, {_AGG_SQL[agg]}({v}) AS {_q(self.dialect, out_col)} FROM {table} WHERE {' AND '.join(where)} GROUP BY {g}"
        else:
            # portable last/first: the value at the max/min date per group (a window function, every dialect here)
            order = "DESC" if agg == "last" else "ASC"
            sql = (f"SELECT {g}, {v} AS {_q(self.dialect, out_col)} FROM (SELECT {g}, {v}, "
                   f"ROW_NUMBER() OVER (PARTITION BY {g} ORDER BY {d} {order}) AS rn FROM {table} "
                   f"WHERE {' AND '.join(where)}) t WHERE rn = 1")
        df = self._execute(sql, params)
        df.columns = [group_col, out_col]
        return df

    def changed_since(self, entity: str, since: datetime | date, *, as_of: date | None = None) -> pd.DataFrame:
        """Rows whose event-time field is strictly after `since` (and at or
        before as_of, if given). Raises for an entity with no event-time
        field -- change detection is declared per entity, never faked."""
        spec = self._entity_spec(entity)
        time_field = (spec.get("time_semantics") or {}).get("event_time_field")
        if not time_field:
            raise DataSourceError(f"'{entity}' declares no event_time_field; change detection not supported for it")
        p = DIALECTS[self.dialect]["param"]
        where, params = [f"{_q(self.dialect, time_field)} > {p}"], [since.isoformat() if hasattr(since, "isoformat") else str(since)]
        if as_of is not None:
            where.append(f"{_q(self.dialect, time_field)} <= {p}"); params.append(as_of.isoformat())
        cols = list(spec.get("required_columns", {}).keys()) + list(spec.get("optional_columns", {}).keys())
        col_sql = ", ".join(_q(self.dialect, c) for c in cols) if cols else "*"
        df = self._execute(f"SELECT {col_sql} FROM {self._table_sql(spec)} WHERE {' AND '.join(where)}", params)
        df.columns = [str(c) for c in df.columns]
        return df


class AthenaSource(SqlSource):
    """Glue catalog + Athena, through pyathena -- the same SQL, `athena`
    dialect. `default_schema` is the Glue database. NOT RUN."""

    def __init__(self, entity_map_ref: str, *, glue_database: str, connection_ref: str | None = None,
                 athena_workgroup: str | None = None, **kw):
        super().__init__("athena", entity_map_ref, connection_ref=connection_ref, default_schema=glue_database,
                         athena_workgroup=athena_workgroup, **kw)
