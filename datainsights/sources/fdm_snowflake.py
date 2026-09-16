"""
FdmSnowflakeSource -- the FDM-shaped equivalent of
datainsights/sources/snowflake_source.py (which serves the LEGACY
schema). Same DataSource contract, same method names as
FdmLocalSource, so every detector, agent tool, and correlation function
above this layer works against it unchanged.

*** STATUS: NOT RUN. *** No query has ever executed against a real
Snowflake account from this repo -- no credentials exist in this
environment, and per CLAUDE.md converting the trial to paid is not
authorized. This is the written, reviewable contract for how the swap
happens, not a verified integration. Treat every claim below about
Snowflake behaviour as a design intention until someone actually runs it
on the NatWest VDI and reports back.

THE POINT OF THIS FILE: prove the domain-segregation design actually
extends. config/entities_fdm.yaml tags every entity with `domain:`
(kernel / lending / ...). FdmLocalSource resolves that to a FOLDER;
this class resolves the SAME tag to a SCHEMA. Nothing above the
DataSource layer knows or cares which one it got -- that is the whole
extensibility claim, made concrete rather than asserted.

Mapping is configured, not hardcoded: `domain_schema_map` is passed in,
so the real NatWest schema names (ENT_PRD.TIER0_PRS for kernel, a
domain-specific NPDM schema for lending, wherever CRADLE lands for a
future risk domain) are supplied at wiring time by whoever actually
knows them -- this file does not guess them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import yaml

from datainsights.sources.base import (
    BatchProvenance,
    DataSource,
    DataSourceError,
    SourceCapabilities,
)
from datainsights.sources.fdm_local import _refuse_if_fincrime

DEFAULT_STATEMENT_ROW_LIMIT = 50_000


class FdmSnowflakeSource(DataSource):
    def __init__(self, connection_ref: str, entity_map_ref: str,
                 domain_schema_map: dict[str, str],
                 statement_row_limit: int = DEFAULT_STATEMENT_ROW_LIMIT,
                 query_timeout_seconds: int = 30):
        """`domain_schema_map` maps each `domain:` value in
        config/entities_fdm.yaml to a fully-qualified Snowflake schema,
        e.g. {"kernel": "ENT_PRD.TIER0_PRS", "lending": "ENT_PRD.NPDM_LENDING"}.
        Refuses to construct if any domain in the contract has no mapping --
        failing closed beats silently reading from the wrong schema."""
        with open(entity_map_ref) as f:
            self._contract = yaml.safe_load(f)

        contract_domains = {
            spec["domain"] for spec in self._contract.get("entities", {}).values()
        }
        unmapped = contract_domains - set(domain_schema_map)
        if unmapped:
            raise DataSourceError(
                f"No Snowflake schema mapped for domain(s) {sorted(unmapped)}. "
                f"Every `domain:` in {entity_map_ref} needs an entry in "
                f"domain_schema_map -- refusing to guess a schema name."
            )
        for domain, schema in domain_schema_map.items():
            _refuse_if_fincrime(schema)

        self.connection_ref = connection_ref
        self.domain_schema_map = dict(domain_schema_map)
        self.statement_row_limit = statement_row_limit
        self.query_timeout_seconds = query_timeout_seconds
        self._con = None  # lazy -- constructing this object must not connect

    # ------------------------------------------------------------------

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            backend="fdm_snowflake",
            supports_bounded_time_window=True,
            supports_change_detection=False,
            read_only=True,
        )

    def _entity_spec(self, entity: str) -> dict:
        entities = self._contract.get("entities", {})
        if entity not in entities:
            raise DataSourceError(
                f"'{entity}' is not in the FDM source contract "
                f"({list(entities.keys())})."
            )
        return entities[entity]

    def _qualified_table(self, entity: str) -> str:
        """{schema for this entity's domain}.{physical_table} -- the
        Snowflake analogue of FdmLocalSource's {data_dir}/{domain}/
        {physical_table}.csv. Both resolve the SAME contract field."""
        spec = self._entity_spec(entity)
        physical_table = spec["physical_table"]
        _refuse_if_fincrime(physical_table)
        schema = self.domain_schema_map[spec["domain"]]
        return f"{schema}.{physical_table}"

    def _connect(self):
        if self._con is not None:
            return self._con
        from datainsights.config import resolve_snowflake_connection  # fail closed if unset

        params = resolve_snowflake_connection(self.connection_ref)
        import snowflake.connector

        self._con = snowflake.connector.connect(
            account=params.account, user=params.user, password=params.password,
            warehouse=params.warehouse, database=params.database,
            schema=params.schema_, role=params.role,
        )
        cur = self._con.cursor()
        cur.execute(
            f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(self.query_timeout_seconds)}"
        )
        return self._con

    def read_entity(
        self,
        entity: str,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        columns: Optional[list[str]] = None,
        allow_unbounded: bool = False,
        query_tag: str = "datainsights_fdm_poc",
    ) -> tuple[pd.DataFrame, BatchProvenance]:
        spec = self._entity_spec(entity)
        table = self._qualified_table(entity)

        required = list(spec.get("required_columns", {}).keys())
        select_cols = columns if columns else required
        col_sql = ", ".join(select_cols) if select_cols else "*"

        time_field = (spec.get("time_semantics") or {}).get("event_time_field")
        bounded = start_date is not None or end_date is not None
        if not bounded and not allow_unbounded:
            raise DataSourceError(
                f"Unbounded read of '{entity}' refused by default -- pass "
                f"start_date/end_date or allow_unbounded=True explicitly. "
                f"(Same guard as FdmLocalSource, and it matters more here: "
                f"an unbounded scan of a real ENT_PRD table costs warehouse "
                f"credits.)"
            )

        where_sql, params = "", []
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
        query = (f"SELECT {col_sql} FROM {table} {where_sql} "
                 f"LIMIT {int(self.statement_row_limit) + 1}")
        cur.execute(query, params)
        df = cur.fetch_pandas_all()
        if len(df) > self.statement_row_limit:
            raise DataSourceError(
                f"'{entity}' returned more than {self.statement_row_limit:,} rows -- "
                f"narrow the window rather than raising the limit blindly."
            )

        missing_required = [c for c in required if c in select_cols and c not in df.columns]
        if missing_required:
            raise DataSourceError(
                f"'{entity}' is missing required contract columns: {missing_required}"
            )

        provenance = BatchProvenance(
            backend="fdm_snowflake",
            entity=entity,
            extracted_as_of=datetime.now(timezone.utc).isoformat(),
            row_count=len(df),
            source_identity=f"fdm_snowflake:{table}",
        )
        return df, provenance

    # ------------------------------------------------------------------
    # As-at + Slot A methods -- SAME names/signatures as FdmLocalSource.
    # This symmetry is what makes the swap a wiring change rather than a
    # rewrite; tests/test_fdm_source_conformance.py asserts it mechanically
    # so the two cannot silently drift apart.
    # ------------------------------------------------------------------

    def as_at(self, entity: str, as_at: date, key_columns: list[str]) -> pd.DataFrame:
        spec = self._entity_spec(entity)
        required = set(spec.get("required_columns", {}).keys())
        if not {"EFFECTIVE_START_DT", "EFFECTIVE_END_DT"} <= required:
            raise DataSourceError(
                f"'{entity}' is not bi-temporal -- as_at() doesn't apply; use read_entity()."
            )
        table = self._qualified_table(entity)
        con = self._connect()
        cur = con.cursor()
        # Exactly the FDM Join Backbone as-at predicate from
        # docs/decision_record.md -- the same one FdmLocalSource applies.
        cur.execute(
            f"SELECT * FROM {table} "
            f"WHERE EFFECTIVE_START_DT <= %s "
            f"  AND (EFFECTIVE_END_DT IS NULL OR EFFECTIVE_END_DT > %s) "
            f"LIMIT {int(self.statement_row_limit) + 1}",
            [as_at.isoformat(), as_at.isoformat()],
        )
        df = cur.fetch_pandas_all()
        dupes = df.duplicated(subset=key_columns, keep=False)
        if dupes.any():
            raise DataSourceError(
                f"as_at('{entity}', {as_at}) returned overlapping versions for "
                f"{key_columns} -- malformed Type 2 SCD in the source."
            )
        return df

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
            "SLOT A1: risk_measure not implemented -- no column DDL captured for "
            "PARTY_METRIC or the CRADLE *_MODEL_DATA tables. Same status as "
            "FdmLocalSource; see docs/decision_record.md Tab 6 Slot A1."
        )

    def treasury_position(self, as_at_date: date) -> pd.DataFrame:
        raise NotImplementedError(
            "SLOT A2: source unknown -- no confirmed C&I client-facing treasury "
            "source exists. TILAPI is the bank's own BoE position, not client "
            "data. See docs/decision_record.md Tab 5 Phase 6 ('blocked')."
        )

    def party_group(self, as_at_date: date) -> pd.DataFrame:
        raise NotImplementedError(
            "party_group deferred -- no detector in scope needs it yet "
            "(Treasury's group_cash_pooling is blocked on SLOT A2)."
        )
