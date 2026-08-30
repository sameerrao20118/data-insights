"""
DataSource contract. Small, typed interface -- not a framework. Any backend
(offline_local today, snowflake next, an AWS/Athena adapter later) must
implement this; detection/analytics code depends only on this interface,
never on a specific backend's client library.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class SourceCapabilities:
    backend: str
    supports_bounded_time_window: bool
    supports_change_detection: bool  # false = full-table read only, no CDC
    read_only: bool = True


@dataclass(frozen=True)
class BatchProvenance:
    """Attached to every batch a DataSource returns, so downstream code can
    tell what it's looking at without re-deriving it. Required by the
    "cache with source identity, extraction-as-of time, content/version
    metadata" instruction."""
    backend: str
    entity: str
    extracted_as_of: str  # ISO 8601 timestamp, when this read happened
    row_count: int
    source_identity: str  # e.g. "offline_local:data_generator/output" or
                           # "snowflake:POC_DB.POC_SCHEMA"


class DataSourceError(Exception):
    """Raised for contract violations: missing required columns, an entity
    not in the contract, or a request for a protected path."""


class DataSource(ABC):
    @abstractmethod
    def capabilities(self) -> SourceCapabilities: ...

    @abstractmethod
    def read_entity(
        self,
        entity: str,
        *,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        columns: Optional[list[str]] = None,
    ) -> tuple[pd.DataFrame, BatchProvenance]:
        """Read a bounded batch of one logical entity (per config/entities.yaml).

        start_date/end_date bound the entity's event_time_field where the
        contract defines one; omitting both is a full-table read and
        implementations should refuse it above a configured row/byte size
        rather than silently streaming an unbounded table.
        """
        ...
