"""
ExternalEventSource: the same interface pattern as DataSource
(datainsights/sources/base.py), for exogenous events instead of a client's
own transactions. One implementation exists today
(SimulatedExternalEventSource, reading external_events/output/). Real
sources -- a market data API, a news API, a social feed -- implement the
same three methods and slot in without touching
detection_engine/external_macro_event.py.

Deliberately a SEPARATE interface from DataSource, not a forced subclass:
exogenous events have a different grain (one event affects many clients by
sector/country match, not one row per client) and no per-account
provenance. Forcing them into DataSource's contract would blur that
distinction rather than express it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional

import pandas as pd

REQUIRED_COLUMNS = [
    "event_id", "event_date", "event_type", "source_name", "real_source_type",
    "affected_country", "affected_sector", "direction", "severity",
    "headline", "description",
]


@dataclass(frozen=True)
class ExternalEventBatchProvenance:
    backend: str
    extracted_as_of: str
    row_count: int
    source_identity: str


class ExternalEventSourceError(Exception):
    pass


class ExternalEventSource(ABC):
    @abstractmethod
    def read_events(
        self, *, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> tuple[pd.DataFrame, ExternalEventBatchProvenance]:
        """Return events with REQUIRED_COLUMNS present. affected_country/
        affected_sector may be empty string, meaning 'applies broadly' --
        the matching layer (external_macro_event.py) treats an empty value
        as a wildcard, not a missing value."""
        ...

    @abstractmethod
    def event_type_catalog(self) -> dict:
        """Returns the event-type -> real-source mapping this backend
        knows about, so callers (and narrative prompts) can cite where a
        signal would really come from in production."""
        ...


class SimulatedExternalEventSource(ExternalEventSource):
    def __init__(self, csv_path: str):
        self.csv_path = csv_path

    def read_events(self, *, start_date: Optional[date] = None, end_date: Optional[date] = None):
        df = pd.read_csv(self.csv_path, dtype={"affected_country": str, "affected_sector": str})
        df["affected_country"] = df["affected_country"].fillna("")
        df["affected_sector"] = df["affected_sector"].fillna("")
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ExternalEventSourceError(f"Missing required columns: {missing}")

        if start_date or end_date:
            d = pd.to_datetime(df["event_date"])
            if start_date:
                df = df[d >= pd.Timestamp(start_date)]
                d = pd.to_datetime(df["event_date"])
            if end_date:
                df = df[d <= pd.Timestamp(end_date)]

        from datetime import datetime, timezone
        provenance = ExternalEventBatchProvenance(
            backend="simulated",
            extracted_as_of=datetime.now(timezone.utc).isoformat(),
            row_count=len(df),
            source_identity=f"simulated:{self.csv_path}",
        )
        return df.reset_index(drop=True), provenance

    def event_type_catalog(self) -> dict:
        from external_events.simulate_external_events import EVENT_TYPE_CATALOG
        return {k: v["real_source"] for k, v in EVENT_TYPE_CATALOG.items()}


# --- Extension points for real sources (NOT implemented -- see README) ---
#
# class MarketDataEventSource(ExternalEventSource):
#     """e.g. ECB SDMX API, Eurostat -- structured, high-confidence, no LLM
#     extraction needed. Map API fields directly to REQUIRED_COLUMNS."""
#
# class NewsEventSource(ExternalEventSource):
#     """e.g. GDELT, a news API -- needs an extraction step (LLM or NLP)
#     to turn raw articles into REQUIRED_COLUMNS before this interface
#     is satisfied. The extraction step is a NEW component, not part of
#     this interface -- read_events() must still return already-structured
#     rows. See docs/objective.md's extraction/matching/review split."""
#
# class SocialFeedEventSource(ExternalEventSource):
#     """e.g. a Twitter/X-like feed -- highest noise, needs the same
#     extraction step as NewsEventSource plus much more aggressive
#     confidence filtering before anything reaches a client match."""
