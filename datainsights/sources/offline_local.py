"""
Offline local DataSource: reads CSVs produced by data_generator/generate_data.py
through DuckDB, validated against config/entities.yaml. This is the
"offline_ollama" profile's source backend -- no network calls.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import duckdb
import pandas as pd
import yaml

from datainsights.sources.base import (
    BatchProvenance,
    DataSource,
    DataSourceError,
    SourceCapabilities,
)

# Any entity/table access under this path is refused outright, regardless
# of what the contract or caller asks for. This is the code-level backstop
# for the directory boundary described in
# data_generator/output/protected_evaluator_only/README.md.
PROTECTED_PATH_MARKER = "protected_evaluator_only"

# Full-table reads (no start_date/end_date bound) are refused above this
# many rows unless explicitly overridden -- "do not... export entire
# datasets without size checks."
DEFAULT_MAX_UNBOUNDED_ROWS = 50_000


class OfflineLocalSource(DataSource):
    def __init__(self, data_dir: str, entity_map_ref: str):
        self.data_dir = Path(data_dir).resolve()
        if PROTECTED_PATH_MARKER in str(self.data_dir):
            raise DataSourceError(
                f"Refusing to construct a DataSource rooted at a protected "
                f"path: {self.data_dir}"
            )
        with open(entity_map_ref) as f:
            self._contract = yaml.safe_load(f)
        self._con = duckdb.connect(database=":memory:")

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            backend="offline_local",
            supports_bounded_time_window=True,
            supports_change_detection=False,  # plain CSVs, no CDC metadata
            read_only=True,
        )

    def _entity_spec(self, entity: str) -> dict:
        entities = self._contract.get("entities", {})
        if entity not in entities:
            raise DataSourceError(
                f"'{entity}' is not in the source contract "
                f"({list(entities.keys())}). trigger_events is deliberately "
                f"excluded -- it is evaluator-only ground truth, never a "
                f"DataSource input."
            )
        return entities[entity]

    def _csv_path(self, physical_table: str) -> Path:
        path = self.data_dir / f"{physical_table}.csv"
        if PROTECTED_PATH_MARKER in str(path):
            raise DataSourceError(f"Refusing to read protected path: {path}")
        if not path.exists():
            raise DataSourceError(f"Expected file not found: {path}")
        return path

    def read_entity(
        self,
        entity: str,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        columns: Optional[list[str]] = None,
        allow_unbounded: bool = False,
    ) -> tuple[pd.DataFrame, BatchProvenance]:
        spec = self._entity_spec(entity)
        path = self._csv_path(spec["physical_table"])

        required = list(spec.get("required_columns", {}).keys())
        optional = list(spec.get("optional_columns", {}).keys())
        select_cols = columns if columns else required + optional

        time_field = (spec.get("time_semantics") or {}).get("event_time_field")
        bounded = start_date is not None or end_date is not None

        if not bounded and not allow_unbounded:
            probe = self._con.execute(
                f"SELECT count(*) FROM read_csv_auto('{path.as_posix()}')"
            ).fetchone()[0]
            if probe > DEFAULT_MAX_UNBOUNDED_ROWS:
                raise DataSourceError(
                    f"Unbounded read of '{entity}' would return {probe:,} rows "
                    f"(> {DEFAULT_MAX_UNBOUNDED_ROWS:,}). Pass start_date/"
                    f"end_date, or allow_unbounded=True if you really mean it."
                )

        col_sql = ", ".join(select_cols) if select_cols else "*"
        where_sql = ""
        params: list = []
        if bounded and time_field:
            clauses = []
            if start_date is not None:
                clauses.append(f"{time_field} >= ?")
                params.append(start_date.isoformat())
            if end_date is not None:
                clauses.append(f"{time_field} <= ?")
                params.append(end_date.isoformat())
            where_sql = "WHERE " + " AND ".join(clauses)

        query = f"SELECT {col_sql} FROM read_csv_auto('{path.as_posix()}') {where_sql}"
        df = self._con.execute(query, params).fetchdf()

        missing_required = [c for c in required if c not in df.columns]
        if missing_required:
            raise DataSourceError(
                f"'{entity}' is missing required contract columns: {missing_required}"
            )

        provenance = BatchProvenance(
            backend="offline_local",
            entity=entity,
            extracted_as_of=datetime.now(timezone.utc).isoformat(),
            row_count=len(df),
            source_identity=f"offline_local:{self.data_dir}",
        )
        return df, provenance
