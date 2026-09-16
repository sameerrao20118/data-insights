"""
RunScopedCache -- wraps any FDM DataSource (FdmLocalSource today,
FdmSnowflakeSource on the VDI) so each distinct query executes ONCE per
run, no matter how many clients' tools ask for it.

Why this exists, measured rather than assumed: the agentic pipeline did
20.7 physical table scans PER CLIENT (60 clients -> 1,240 scans; 422 of
them re-reading AGREEMENT). Each agent tool asks the source for the whole
table/window and then filters to one client. In-process DuckDB hides that
cost; against Snowflake it is an N+1 query pattern -- ~41k warehouse
queries at 2,000 clients, ~1M at 50,000.

With this wrapper the number of physical reads is bounded by the number
of DISTINCT (method, arguments) combinations in a run -- a handful of
set-based queries for the whole book -- and per-client filtering happens
in memory. Tools, agents, detectors and the correlation layer are
unchanged: they still call source.agreement(as_of), source.daily_balance(
start, end), etc. That is deliberate -- the fix lives at the DataSource
boundary, which is the one layer designed to be swapped.

Scope: one run, one as-of. Create a fresh cache per run; never share one
across runs or as-of dates (it would serve a stale snapshot). Frames are
returned as-is; pandas copy-on-write (pandas >= 3) means a caller's
column assignment on a filtered slice cannot corrupt the cached frame.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

CACHED_METHODS = (
    "as_at", "party", "agreement", "daily_balance", "financial_event",
    "party_demographic", "party_locator", "collateral_item_value",
)


class RunScopedCache:
    def __init__(self, source):
        self._source = source
        self._memo: dict[tuple, object] = {}
        self.hits = 0
        self.misses = 0

    def _cached(self, key: tuple, compute):
        if key in self._memo:
            self.hits += 1
            return self._memo[key]
        self.misses += 1
        value = compute()
        self._memo[key] = value
        return value

    # --- DataSource surface (same names/signatures as FdmLocalSource) -----

    def capabilities(self):
        return self._source.capabilities()

    def read_entity(self, entity: str, *, start_date: Optional[date] = None,
                    end_date: Optional[date] = None, columns: Optional[list[str]] = None,
                    allow_unbounded: bool = False):
        key = ("read_entity", entity, start_date, end_date,
               tuple(columns) if columns else None, allow_unbounded)
        return self._cached(key, lambda: self._source.read_entity(
            entity, start_date=start_date, end_date=end_date,
            columns=columns, allow_unbounded=allow_unbounded))

    def as_at(self, entity: str, as_at: date, key_columns: list[str]):
        key = ("as_at", entity, as_at, tuple(key_columns))
        return self._cached(key, lambda: self._source.as_at(entity, as_at, key_columns))

    def party(self, as_at_date: date):
        return self._cached(("party", as_at_date), lambda: self._source.party(as_at_date))

    def agreement(self, as_at_date: date):
        return self._cached(("agreement", as_at_date), lambda: self._source.agreement(as_at_date))

    def daily_balance(self, start_date: date, end_date: date):
        return self._cached(("daily_balance", start_date, end_date),
                            lambda: self._source.daily_balance(start_date, end_date))

    def financial_event(self, start_date: date, end_date: date):
        return self._cached(("financial_event", start_date, end_date),
                            lambda: self._source.financial_event(start_date, end_date))

    def party_demographic(self):
        return self._cached(("party_demographic",), self._source.party_demographic)

    def party_locator(self):
        return self._cached(("party_locator",), self._source.party_locator)

    def collateral_item_value(self, as_at_date: date):
        return self._cached(("collateral_item_value", as_at_date),
                            lambda: self._source.collateral_item_value(as_at_date))

    # Deferred/blocked slots are never cached -- they must raise every time,
    # exactly as the underlying source does.
    def risk_measure(self, as_at_date: date):
        return self._source.risk_measure(as_at_date)

    def treasury_position(self, as_at_date: date):
        return self._source.treasury_position(as_at_date)

    def party_group(self, as_at_date: date):
        return self._source.party_group(as_at_date)
