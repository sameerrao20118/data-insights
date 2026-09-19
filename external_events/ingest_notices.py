"""
A1 ingestion entrypoint (docs/agentic_plan.md): runs notice text through
event_extraction_agent.extract_event(), stores the result via
extracted_event_store.store_result(). This is the ONLY place text
becomes an event this repo trusts -- everything downstream
(exposure_qualifier.qualifies(), the correlation layer) is unchanged and
untouched by this module.

Run: python -m external_events.ingest_notices
"""

from __future__ import annotations

import os
from datetime import date

from agents.model_factory import get_model
from datainsights.runtime import build_runtime
from external_events.event_extraction_agent import extract_event
from external_events.extracted_event_store import store_result
from external_events.sample_notices import NOTICE_MATCHING_FIXTURE

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm_extracted", "extracted_events.csv")
REVIEW_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm_extracted", "review_queue.csv")


def ingest(notices, model, *, events_path: str = EVENTS_PATH, review_path: str = REVIEW_PATH,
           ingest_date: date | None = None, log=print) -> list:
    """R12: the A1 extraction as a callable step. `notices` is a list of
    (source_ref, text). Each result lands in exactly one of the two
    stores; a stored extracted event is what the next incremental run
    (datainsights/runs.py) picks up to enqueue the clients it qualifies."""
    results = []
    for source_ref, text in notices:
        result = extract_event(text, source_name="manual_ingest", source_ref=source_ref,
                                model=model, ingest_date=ingest_date or date.today())
        dest = store_result(result, events_path, review_path)
        log(f"[{source_ref}] status={result.status} -> {dest}")
        if result.event:
            e = result.event
            log(f"  {e.event_type} | {e.event_date} | sector={e.affected_sector} "
                f"country={e.affected_country} value=EUR {e.estimated_value_eur:,.0f}")
        if result.problems:
            log(f"  problems: {result.problems}")
        results.append(result)
    return results


def main():
    model = get_model(build_runtime("fdm_local").model_config)
    print("=" * 78)
    print("A1 -- ingesting notice text into a validated ExogenousEvent")
    print("Synthetic notice, written for this repo. Not a real tender.")
    print("=" * 78)
    ingest([("sample_ted_notice", NOTICE_MATCHING_FIXTURE)], model, ingest_date=date(2025, 7, 10))


if __name__ == "__main__":
    main()
