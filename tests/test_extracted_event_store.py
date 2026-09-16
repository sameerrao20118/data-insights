"""
A1 verification for external_events/extracted_event_store.py: an
"extracted" result lands in the events file (and reads back as a real
ExogenousEvent, drop-in for exposure_qualifier.load_events()'s shape);
a "needs_review" result lands in the review file and NEVER in the
events file, regardless of any code path.
"""

import os
from datetime import date

from external_events.event_extraction_agent import ExtractionResult
from external_events.exposure_qualifier import ExogenousEvent
from external_events.extracted_event_store import read_extracted_events, store_result


def extracted_result():
    event = ExogenousEvent(event_id="e1", event_date=date(2026, 6, 14), event_type="public_tender_award",
                           affected_country="ES", affected_sector="C", severity=4,
                           estimated_value_eur=3_200_000.0)
    return ExtractionResult(source_name="test_source", source_ref="ref1", event=event,
                            quote="A tender worth EUR 3.2 million.", confidence=0.9, status="extracted")


def review_result():
    return ExtractionResult(source_name="test_source", source_ref="ref2", event=None, quote="unclear text",
                            confidence=0.3, status="needs_review", problems=["no value found"])


def test_extracted_result_lands_in_events_file_and_reads_back(tmp_path):
    events_path = str(tmp_path / "events.csv")
    review_path = str(tmp_path / "review.csv")

    dest = store_result(extracted_result(), events_path, review_path)
    assert dest == events_path
    assert not os.path.exists(review_path)

    events = read_extracted_events(events_path)
    assert len(events) == 1
    assert events[0].event_id == "e1"
    assert events[0].estimated_value_eur == 3_200_000.0


def test_needs_review_result_never_lands_in_events_file(tmp_path):
    events_path = str(tmp_path / "events.csv")
    review_path = str(tmp_path / "review.csv")

    dest = store_result(review_result(), events_path, review_path)
    assert dest == review_path
    assert not os.path.exists(events_path)
    assert read_extracted_events(events_path) == []


def test_multiple_results_append_correctly(tmp_path):
    events_path = str(tmp_path / "events.csv")
    review_path = str(tmp_path / "review.csv")

    store_result(extracted_result(), events_path, review_path)
    store_result(review_result(), events_path, review_path)
    store_result(extracted_result(), events_path, review_path)

    assert len(read_extracted_events(events_path)) == 2
    with open(review_path) as f:
        assert len(f.readlines()) == 2  # header + 1 review row
