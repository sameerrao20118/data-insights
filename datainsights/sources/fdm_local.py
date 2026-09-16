"""
FdmLocalSource: reads the FDM-shaped CSVs produced by
data_generator/fdm/generate_fdm.py through DuckDB, validated against
config/entities_fdm.yaml.

Modeled directly on datainsights/sources/offline_local.py -- same
protected-path refusal, same unbounded-read guard, same DuckDB-over-CSV
mechanics. This is a parallel adapter, not a replacement: OfflineLocalSource
still serves the legacy schema; this one serves the FDM-shaped path built
per docs/decision_record.md. See config/entities_fdm.yaml's header for
which entities are in scope here and why.

Adds two things offline_local.py doesn't need:
  - an as-at join helper, because the FDM entities are bi-temporal
    (EFFECTIVE_START_DT/EFFECTIVE_END_DT) where the legacy schema mostly
    isn't -- see docs/decision_record.md's "FDM Join Backbone" /
    "bi-temporal is load-bearing" sections.
  - named Slot-A methods (docs/decision_record.md Tab 6, "Slot Type A --
    new source domain") so a future real ENT_PRD.TIER0_PRS-backed source
    implements the same shape. Slots this pass has no data for raise
    NotImplementedError with the reason, rather than a fake empty
    DataFrame -- silently returning "no rows" would be indistinguishable
    from "this client genuinely has none," which is a data-integrity
    hazard for anything relying on absence-of-evidence.
"""

from __future__ import annotations

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

PROTECTED_PATH_MARKER = "protected_evaluator_only"
DEFAULT_MAX_UNBOUNDED_ROWS = 50_000

# Hard boundary (CLAUDE.md; docs/decision_record.md D5 and "Boundaries
# Enforced In Code"). Not reachable through this contract today -- no
# entity in config/entities_fdm.yaml names these -- but enforced here too,
# defensively, so a future contract edit can't accidentally reopen it by
# just adding an entity whose physical_table happens to match.
FINCRIME_REFUSED_TABLES = {"fsa_prd_fincrime", "fsa_prd_fc_analytics", "pep_prs"}
FINCRIME_REFUSED_SUFFIX = "_xdo"


def _refuse_if_fincrime(physical_table: str) -> None:
    name = physical_table.lower()
    if name in FINCRIME_REFUSED_TABLES or name.endswith(FINCRIME_REFUSED_SUFFIX):
        raise DataSourceError(
            f"Refusing to read '{physical_table}': resolves to the FinCrime "
            f"governance path (FSA_PRD_FINCRIME / FSA_PRD_FC_ANALYTICS / "
            f"PEP_PRS / *_XDO). This PoC has no data contract, named "
            f"provider, or registered purpose for that path -- see "
            f"docs/decision_record.md D5."
        )


class FdmLocalSource(DataSource):
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
            backend="fdm_local",
            supports_bounded_time_window=True,
            supports_change_detection=False,
            read_only=True,
        )

    def _entity_spec(self, entity: str) -> dict:
        entities = self._contract.get("entities", {})
        if entity not in entities:
            raise DataSourceError(
                f"'{entity}' is not in the FDM source contract "
                f"({list(entities.keys())}). See config/entities_fdm.yaml's "
                f"header for what's deferred and why."
            )
        return entities[entity]

    def _csv_path(self, physical_table: str, domain: str) -> Path:
        """Resolves to {data_dir}/{domain}/{physical_table}.csv --
        `domain` (kernel, lending, ...) is the extensibility point: a
        future SnowflakeSource honoring the same config/entities_fdm.yaml
        `domain` field maps it to a schema/database instead of a folder.
        See that file's header note."""
        _refuse_if_fincrime(physical_table)
        path = self.data_dir / domain / f"{physical_table}.csv"
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
        path = self._csv_path(spec["physical_table"], spec["domain"])

        required = list(spec.get("required_columns", {}).keys())
        select_cols = columns if columns else required

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

        # Only validate contract compliance for columns actually requested
        # -- a caller-supplied `columns` projection legitimately narrows
        # what's returned, and checking the full contract's required list
        # against a deliberately narrower result would reject a valid,
        # explicit projection.
        missing_required = [c for c in required if c in select_cols and c not in df.columns]
        if missing_required:
            raise DataSourceError(
                f"'{entity}' is missing required contract columns: {missing_required}"
            )

        provenance = BatchProvenance(
            backend="fdm_local",
            entity=entity,
            extracted_as_of=datetime.now(timezone.utc).isoformat(),
            row_count=len(df),
            source_identity=f"fdm_local:{self.data_dir}",
        )
        return df, provenance

    # ------------------------------------------------------------------
    # As-at join backbone -- docs/decision_record.md's exact pattern:
    #   EFFECTIVE_START_DT <= :as_at
    #   AND (EFFECTIVE_END_DT IS NULL OR EFFECTIVE_END_DT > :as_at)
    # ------------------------------------------------------------------
    def as_at(self, entity: str, as_at: date, key_columns: list[str]) -> pd.DataFrame:
        """Return exactly the version of each key valid at `as_at`, from a
        bi-temporal entity. `key_columns` is the entity's natural key
        (e.g. ["PRTY_ID"] for PARTY, ["CLTRL_ITEM_ID"] for
        COLLATERAL_ITEM_VALUE) -- NOT including EFFECTIVE_START_DT, since
        that's exactly the column this collapses away.

        Raises if the entity has no EFFECTIVE_START_DT/EFFECTIVE_END_DT
        columns -- calling this on a current-state-only entity is a bug,
        not a valid degenerate case (use read_entity directly instead)."""
        spec = self._entity_spec(entity)
        required = set(spec.get("required_columns", {}).keys())
        if not {"EFFECTIVE_START_DT", "EFFECTIVE_END_DT"} <= required:
            raise DataSourceError(
                f"'{entity}' is not bi-temporal (no EFFECTIVE_START_DT/"
                f"EFFECTIVE_END_DT in its contract) -- as_at() doesn't "
                f"apply; use read_entity()."
            )
        path = self._csv_path(spec["physical_table"], spec["domain"])
        # EFFECTIVE_END_DT is empty-string (not NULL) for a current version
        # in the CSVs this generator writes -- read it as VARCHAR and cast
        # explicitly, since read_csv_auto's type-sniffer infers DATE from
        # the populated rows and then chokes on '' in the rest.
        query = f"""
            SELECT * EXCLUDE (EFFECTIVE_END_DT),
                   TRY_CAST(NULLIF(EFFECTIVE_END_DT, '') AS DATE) AS EFFECTIVE_END_DT
            FROM read_csv_auto('{path.as_posix()}', types={{'EFFECTIVE_END_DT': 'VARCHAR'}})
            WHERE EFFECTIVE_START_DT <= ?
              AND (NULLIF(EFFECTIVE_END_DT, '') IS NULL
                   OR TRY_CAST(EFFECTIVE_END_DT AS DATE) > ?)
        """
        df = self._con.execute(query, [as_at.isoformat(), as_at.isoformat()]).fetchdf()
        dupes = df.duplicated(subset=key_columns, keep=False)
        if dupes.any():
            raise DataSourceError(
                f"as_at('{entity}', {as_at}) returned overlapping versions "
                f"for key(s) {key_columns} -- the generator produced "
                f"overlapping effective-dated ranges, which should be "
                f"impossible for a well-formed Type 2 SCD."
            )
        return df

    # ------------------------------------------------------------------
    # Slot Type A methods (docs/decision_record.md Tab 6). Implemented
    # where this pass has data; NotImplementedError with the reason
    # where it doesn't, per this file's module docstring.
    # ------------------------------------------------------------------
    def party(self, as_at_date: date) -> pd.DataFrame:
        return self.as_at("PARTY", as_at_date, ["PRTY_ID"])

    def agreement(self, as_at_date: date) -> pd.DataFrame:
        return self.as_at("AGREEMENT", as_at_date, ["AGRMNT_ID"])

    def daily_balance(self, start_date: date, end_date: date) -> pd.DataFrame:
        df, _ = self.read_entity("AGREEMENT_DAILY_BALANCE", start_date=start_date, end_date=end_date)
        return df

    def financial_event(self, start_date: date, end_date: date) -> pd.DataFrame:
        df, _ = self.read_entity("EVENT_FINANCIAL", start_date=start_date, end_date=end_date)
        return df

    def party_demographic(self) -> pd.DataFrame:
        df, _ = self.read_entity("PARTY_DEMOGRAPHIC", allow_unbounded=True)
        return df

    def party_locator(self) -> pd.DataFrame:
        df, _ = self.read_entity("PARTY_LOCATOR", allow_unbounded=True)
        return df

    def collateral_item_value(self, as_at_date: date) -> pd.DataFrame:
        return self.as_at("COLLATERAL_ITEM_VALUE", as_at_date, ["CLTRL_ITEM_ID"])

    def risk_measure(self, as_at_date: date) -> pd.DataFrame:
        raise NotImplementedError(
            "SLOT A1: risk_measure not implemented this pass -- no column "
            "DDL was captured for PARTY_METRIC or the CRADLE *_MODEL_DATA "
            "tables (LC_MODEL_DATA, ML_MODEL_DATA, ...). Risk domain "
            "detectors (rating_downgrade, pd_migration, concentration_risk) "
            "are out of scope until real DDL or explicit invented-schema "
            "sign-off exists. See docs/decision_record.md Tab 6, Slot A1."
        )

    def treasury_position(self, as_at_date: date) -> pd.DataFrame:
        raise NotImplementedError(
            "SLOT A2: source unknown -- no confirmed C&I client-facing "
            "treasury source exists. TILAPI is the bank's own Bank of "
            "England position, not client data. Do not build a detector "
            "against it. See docs/decision_record.md Tab 5 Phase 6 "
            "('blocked') and Tab 6 Slot A2."
        )

    def party_group(self, as_at_date: date) -> pd.DataFrame:
        raise NotImplementedError(
            "party_group deferred this pass -- no detector in scope needs "
            "it yet (Treasury's group_cash_pooling signal is blocked on "
            "SLOT A2). Add PARTY_GROUP/PARTY_TO_PARTY_GROUP to "
            "config/entities_fdm.yaml when one does."
        )
