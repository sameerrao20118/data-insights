"""
Storage for agent-extracted events (docs/agentic_plan.md A1) -- append-
only CSVs, local-file today.

Honest gap, not yet closed: unlike datainsights/sources/base.py's
DataSource (a real ABC, two implementations, a conformance test proving
they agree), there is no typed interface here -- read_extracted_events()
and exposure_qualifier.load_events() just happen to return the same
list[ExogenousEvent] shape, by convention, not by a shared contract with
anything checking it. Formalizing this (an EventSource ABC mirroring
DataSource's pattern) is real, undone work for whenever a second store
(Snowflake/S3) is added -- see docs/agentic_plan.md's corrected A1 note.

Two files, never merged:
  - EVENTS_PATH   -- validated ExogenousEvents. Same columns
                     exposure_qualifier.load_events() already reads, so
                     an extracted event is a drop-in for the generator's
                     tender_events.csv wherever it's consumed. Plus
                     provenance columns (source_name, source_ref, quote,
                     confidence, extracted_at) that load_events() ignores
                     but this module's own reader keeps.
  - REVIEW_PATH   -- everything extract_event() marked needs_review, with
                     its problems -- a queue a person reads, NEVER
                     auto-promoted to EVENTS_PATH by any code path here.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

from external_events.event_extraction_agent import ExtractionResult
from external_events.exposure_qualifier import ExogenousEvent

EVENT_COLUMNS = ["event_id", "event_date", "event_type", "affected_country", "affected_sector",
                 "severity", "estimated_value_eur", "source_name", "source_ref", "quote",
                 "confidence", "extracted_at"]

REVIEW_COLUMNS = ["source_name", "source_ref", "status", "problems", "quote", "confidence", "extracted_at"]


def _append(path: str, columns: list[str], row: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    is_new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        if is_new:
            writer.writeheader()
        writer.writerow(row)


def store_result(result: ExtractionResult, events_path: str, review_path: str) -> str:
    """Writes `result` to exactly one of the two files, per its status.
    Returns which path it went to."""
    stamp = datetime.now(timezone.utc).isoformat()
    if result.status == "extracted":
        e = result.event
        _append(events_path, EVENT_COLUMNS, {
            "event_id": e.event_id, "event_date": e.event_date.isoformat(), "event_type": e.event_type,
            "affected_country": e.affected_country, "affected_sector": e.affected_sector,
            "severity": e.severity, "estimated_value_eur": e.estimated_value_eur,
            "source_name": result.source_name, "source_ref": result.source_ref,
            "quote": result.quote, "confidence": result.confidence, "extracted_at": stamp,
        })
        return events_path
    _append(review_path, REVIEW_COLUMNS, {
        "source_name": result.source_name, "source_ref": result.source_ref,
        "status": result.status, "problems": " | ".join(result.problems),
        "quote": result.quote, "confidence": result.confidence, "extracted_at": stamp,
    })
    return review_path


def read_extracted_events(events_path: str) -> list[ExogenousEvent]:
    """Same shape exposure_qualifier.load_events() returns -- an
    extracted-events file is a drop-in wherever a generator events CSV is
    consumed today (agents/demo_fdm_scenario.py, orchestrator.py callers)."""
    from external_events.exposure_qualifier import load_events

    if not os.path.exists(events_path):
        return []
    return load_events(events_path)
