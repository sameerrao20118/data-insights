"""
Phase 5a schema onboarding -- the deterministic profiler
(docs/generalization_plan.md). Reads a directory of CSV files (the
input shape every schema onboarded so far -- `fdm`, `legacy`, `sba` --
actually is) and produces a structural profile per table: columns,
inferred types, key candidates, date columns, a guess at bi-temporal
pairs, and a handful of PII-safe-truncated sample values.

This module is entirely additive and read-only -- it constructs no
DataSource, writes nothing, and never touches config/bindings/,
config/entities_fdm.yaml, or anything else an existing pipeline reads.
Profiling a new directory can never change what `fdm_local`/`legacy`/
`sba_local` already do.

DDL text and Excel data dictionaries (the other two input shapes the
plan names) are real, scoped, undone work -- CSV directories are what
every schema onboarded to this platform so far has actually looked
like, so that's the one this pass builds and proves end to end, rather
than three half-built readers.
"""

from __future__ import annotations

import os
import re
import warnings
from dataclasses import dataclass, field

import pandas as pd

# Column-name patterns that plausibly mark a bi-temporal pair -- matched
# against SORTED column names, not assumed to be adjacent in the file.
_TEMPORAL_START_PATTERNS = re.compile(r"(EFFECTIVE_START|START_DT|OPEN_DT|VALID_FROM)$", re.IGNORECASE)
_TEMPORAL_END_PATTERNS = re.compile(r"(EFFECTIVE_END|END_DT|CLOSE_DT|VALID_TO)$", re.IGNORECASE)
_DATE_LIKE_SUFFIX = re.compile(r"(_DT|_DATE|_AT|DTTM)$", re.IGNORECASE)

MAX_SAMPLE_VALUES = 3
SAMPLE_TRUNCATE_CHARS = 40  # PII-safety: never surface a full free-text value


@dataclass
class ColumnProfile:
    name: str
    inferred_type: str  # "string" | "int" | "float" | "date" | "bool"
    nullable: bool
    is_unique: bool
    sample_values: list[str] = field(default_factory=list)


@dataclass
class TableProfile:
    physical_table: str
    row_count: int
    columns: list[ColumnProfile]
    key_candidates: list[str]       # unique, non-null columns -- primary key guesses
    date_columns: list[str]
    bitemporal_pair: tuple[str, str] | None  # (valid_from, valid_to) guess, or None


def _infer_type(series: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(series):
        return "bool"
    if pd.api.types.is_integer_dtype(series):
        return "int"
    if pd.api.types.is_float_dtype(series):
        return "float"
    non_null = series.dropna()
    if non_null.empty:
        return "string"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            pd.to_datetime(non_null.head(20), errors="raise")
        return "date"
    except (ValueError, TypeError):
        return "string"


def _truncate(value) -> str:
    text = str(value)
    return text if len(text) <= SAMPLE_TRUNCATE_CHARS else text[:SAMPLE_TRUNCATE_CHARS] + "…"


def profile_table(path: str) -> TableProfile:
    """One CSV file -> its TableProfile. `physical_table` is the
    filename without extension, matching config/entities*.yaml's own
    `physical_table:` convention."""
    df = pd.read_csv(path, nrows=5000)  # a bounded sample is enough to profile structure, never the whole file
    physical_table = os.path.splitext(os.path.basename(path))[0]

    columns = []
    for col in df.columns:
        series = df[col]
        columns.append(ColumnProfile(
            name=col, inferred_type=_infer_type(series),
            nullable=bool(series.isna().any()),
            is_unique=bool(series.is_unique) and not series.isna().any(),
            sample_values=[_truncate(v) for v in series.dropna().unique()[:MAX_SAMPLE_VALUES]],
        ))

    key_candidates = [c.name for c in columns if c.is_unique]
    date_columns = [c.name for c in columns if c.inferred_type == "date" or _DATE_LIKE_SUFFIX.search(c.name)]

    bitemporal_pair = None
    starts = [c for c in date_columns if _TEMPORAL_START_PATTERNS.search(c)]
    ends = [c for c in date_columns if _TEMPORAL_END_PATTERNS.search(c)]
    if starts and ends:
        bitemporal_pair = (starts[0], ends[0])

    return TableProfile(physical_table=physical_table, row_count=len(df), columns=columns,
                        key_candidates=key_candidates, date_columns=date_columns,
                        bitemporal_pair=bitemporal_pair)


def profile_directory(data_dir: str) -> dict[str, TableProfile]:
    """Every *.csv directly under data_dir (non-recursive -- matching
    OfflineLocalSource's own flat-directory assumption) -> {physical_table: TableProfile}."""
    profiles = {}
    for name in sorted(os.listdir(data_dir)):
        if name.endswith(".csv"):
            path = os.path.join(data_dir, name)
            profiles[os.path.splitext(name)[0]] = profile_table(path)
    return profiles
