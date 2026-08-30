"""
Snowflake DataSource adapter. Implements the same interface as
OfflineLocalSource so detection_engine/ code needs zero changes when this
is wired up.

*** STATUS: NOT RUN. *** No Snowflake credentials were available to this
session (see datainsights/config.resolve_snowflake_connection -- it fails
closed if env vars are unset, which they are). This file has never
connected to a real Snowflake account. Treat it as a reviewed contract
implementation, not a tested integration, until someone runs it against a
real account and that result is recorded (project instructions section 4:
"Mark AWS execution NOT RUN until an authorized real integration test
succeeds" -- same standard applies here for Snowflake).

To actually use this:
1. In the Snowflake UI, create a warehouse (XSMALL, auto-suspend ~60s), a
   database, and a schema for this POC.
2. Load the CSVs in data_generator/output/ (NOT protected_evaluator_only/)
   into tables matching config/entities.yaml's required_columns.
3. Set env vars: SNOWFLAKE_POC_ACCOUNT, SNOWFLAKE_POC_USER,
   SNOWFLAKE_POC_PASSWORD, SNOWFLAKE_POC_WAREHOUSE, SNOWFLAKE_POC_DATABASE,
   SNOWFLAKE_POC_SCHEMA in your own shell profile -- never in a file that
   gets committed.
4. Run with DATAINSIGHTS_PROFILE=snowflake_trial_ollama.
5. Run the golden conformance cases (once written) against both this and
   OfflineLocalSource and diff the results before trusting this path.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from datainsights.config import SnowflakeConnectionParams, resolve_snowflake_connection
from datainsights.sources.base import (
    BatchProvenance,
    DataSource,
    DataSourceError,
    SourceCapabilities,
)

DEFAULT_MAX_UNBOUNDED_ROWS = 50_000


class SnowflakeSource(DataSource):
    def __init__(self, connection_ref: str, entity_map_ref: str,
                 statement_row_limit: int = DEFAULT_MAX_UNBOUNDED_ROWS,
                 query_timeout_seconds: int = 30):
        import yaml
        self._params: SnowflakeConnectionParams = resolve_snowflake_connection(connection_ref)
        with open(entity_map_ref) as f:
            self._contract = yaml.safe_load(f)
        self._statement_row_limit = statement_row_limit
        self._query_timeout_seconds = query_timeout_seconds
        self._con = None  # lazy connect -- constructing this object costs nothing
                           # remote until read_entity is actually called

    def _connect(self):
        if self._con is not None:
            return self._con
        import snowflake.connector
        self._con = snowflake.connector.connect(
            account=self._params.account,
            user=self._params.user,
            password=self._params.password,
            warehouse=self._params.warehouse,
            database=self._params.database,
            schema=self._params.schema_,
            role=self._params.role,
            login_timeout=self._query_timeout_seconds,
            network_timeout=self._query_timeout_seconds,
        )
        cur = self._con.cursor()
        cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {self._query_timeout_seconds}")
        cur.close()
        return self._con

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            backend="snowflake",
            supports_bounded_time_window=True,
            supports_change_detection=False,  # no CDC/stream wired in this POC
            read_only=True,
        )

    def _entity_spec(self, entity: str) -> dict:
        entities = self._contract.get("entities", {})
        if entity not in entities:
            raise DataSourceError(
                f"'{entity}' is not in the source contract. trigger_events is "
                f"deliberately excluded -- evaluator-only ground truth."
            )
        return entities[entity]

    def read_entity(
        self,
        entity: str,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        columns: Optional[list[str]] = None,
        allow_unbounded: bool = False,
        query_tag: str = "datainsights_poc",
    ) -> tuple[pd.DataFrame, BatchProvenance]:
        spec = self._entity_spec(entity)
        table = spec["physical_table"]
        # Physical identifier is validated against the contract above, not
        # built from arbitrary caller input -- avoids unrestricted string
        # interpolation into SQL (project instructions section 4.1).
        required = list(spec.get("required_columns", {}).keys())
        optional = list(spec.get("optional_columns", {}).keys())
        select_cols = columns if columns else required + optional
        col_sql = ", ".join(select_cols) if select_cols else "*"

        time_field = (spec.get("time_semantics") or {}).get("event_time_field")
        bounded = start_date is not None or end_date is not None
        if not bounded and not allow_unbounded:
            raise DataSourceError(
                "Unbounded Snowflake reads are refused by default -- pass "
                "start_date/end_date or allow_unbounded=True explicitly."
            )

        where_sql = ""
        params: list = []
        if bounded and time_field:
            clauses = []
            if start_date is not None:
                clauses.append(f"{time_field} >= %s")
                params.append(start_date.isoformat())
            if end_date is not None:
                clauses.append(f"{time_field} <= %s")
                params.append(end_date.isoformat())
            where_sql = "WHERE " + " AND ".join(clauses)

        con = self._connect()
        cur = con.cursor()
        cur.execute(f"ALTER SESSION SET QUERY_TAG = '{query_tag}'")
        query = (
            f"SELECT {col_sql} FROM {table} {where_sql} "
            f"LIMIT {self._statement_row_limit + 1}"
        )
        cur.execute(query, params)
        df = cur.fetch_pandas_all()
        cur.close()

        if len(df) > self._statement_row_limit:
            raise DataSourceError(
                f"Query for '{entity}' hit the {self._statement_row_limit:,}-row "
                f"limit -- narrow the date window."
            )

        missing_required = [c for c in required if c not in df.columns.str.lower()]
        if missing_required:
            raise DataSourceError(
                f"'{entity}' is missing required contract columns: {missing_required}"
            )

        from datetime import datetime, timezone
        provenance = BatchProvenance(
            backend="snowflake",
            entity=entity,
            extracted_as_of=datetime.now(timezone.utc).isoformat(),
            row_count=len(df),
            source_identity=f"snowflake:{self._params.database}.{self._params.schema_}",
        )
        return df, provenance

    def close(self):
        if self._con is not None:
            self._con.close()
            self._con = None
