"""
Tests for datainsights/sources/caching.py.

The load-bearing test is test_cache_changes_cost_not_answers: the whole
book produces IDENTICAL recommendations with and without the cache, while
physical reads collapse from per-client to per-distinct-query. A
scalability fix that changed a single recommendation would be a bug.
"""

import os
from datetime import date, timedelta

import pytest
import yaml

from agents.orchestrator import evaluate_book, evaluate_client
from datainsights.sources.caching import RunScopedCache
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")
EVENTS_PATH = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")

pytestmark = pytest.mark.skipif(
    not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS_PATH)),
    reason="run the FDM data + event generators first",
)


class CountingSource(FdmLocalSource):
    """Counts physical reads (read_entity + as_at are the scan paths)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.physical_reads = 0

    def read_entity(self, *a, **k):
        self.physical_reads += 1
        return super().read_entity(*a, **k)

    def as_at(self, *a, **k):
        self.physical_reads += 1
        return super().as_at(*a, **k)


@pytest.fixture(scope="module")
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def event():
    from external_events.exposure_qualifier import load_events
    return load_events(EVENTS_PATH)[0]


def test_cache_changes_cost_not_answers(rules, event):
    """Answers must be identical with and without the cache on the book
    path. The cost claim is measured on the PER-CLIENT path (a fresh
    CanonicalSource per client, what Trace-one-client and any caller
    without evaluate_book gets): there the cache is the only thing
    between O(clients) physical reads and per-distinct-query reads.
    Since R13, evaluate_book shares one CanonicalSource whose own frame
    cache already reads each entity once, so on the book path the source
    cache is nearly redundant -- pinned below rather than asserted as a
    5x saving it no longer provides there."""
    as_of = event.event_date + timedelta(days=90)

    raw = CountingSource(FDM_DIR, CONTRACT_PATH)
    ids = sorted(raw.party(as_of)["PRTY_ID"])[:25]
    raw.physical_reads = 0
    uncached = evaluate_book(ids, source=raw, rules=rules, as_of=as_of, event=event)
    uncached_book_reads = raw.physical_reads

    counted = CountingSource(FDM_DIR, CONTRACT_PATH)
    cached = evaluate_book(ids, source=RunScopedCache(counted), rules=rules, as_of=as_of, event=event)
    assert [e.recommendation for e in cached] == [e.recommendation for e in uncached]
    # R13: the shared CanonicalSource bounds book-path reads to a handful
    # of distinct entities, cache or no cache -- never O(clients).
    assert uncached_book_reads < len(ids), uncached_book_reads

    # Per-client path: the cache is what collapses the reads.
    raw.physical_reads = 0
    for prty_id in ids:
        evaluate_client(prty_id, source=raw, rules=rules, as_of=as_of, event=event, narrate=False)
    per_client_uncached = raw.physical_reads
    counted.physical_reads = 0
    cache = RunScopedCache(counted)
    for prty_id in ids:
        evaluate_client(prty_id, source=cache, rules=rules, as_of=as_of, event=event, narrate=False)
    per_client_cached = counted.physical_reads
    assert per_client_cached < per_client_uncached / 5, (per_client_cached, per_client_uncached)


def test_physical_reads_do_not_grow_with_client_count(rules, event):
    """The scalability property itself: doubling the book must not double
    the physical reads."""
    as_of = event.event_date + timedelta(days=90)
    probe = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    all_ids = sorted(probe.party(as_of)["PRTY_ID"])

    reads = []
    for n in (10, 40):
        counted = CountingSource(FDM_DIR, CONTRACT_PATH)
        evaluate_book(all_ids[:n], source=RunScopedCache(counted), rules=rules, as_of=as_of, event=event)
        reads.append(counted.physical_reads)
    assert reads[1] <= reads[0] + 2, reads


def test_blocked_slots_still_raise_through_the_cache():
    cache = RunScopedCache(FdmLocalSource(FDM_DIR, CONTRACT_PATH))
    with pytest.raises(NotImplementedError, match="SLOT A2"):
        cache.treasury_position(date(2026, 1, 1))
    with pytest.raises(NotImplementedError, match="SLOT A2"):
        cache.treasury_position(date(2026, 1, 1))  # not memoised into silence


def test_cache_exposes_the_shared_datasource_surface():
    from tests.test_fdm_source_conformance import SHARED_SURFACE
    for name in SHARED_SURFACE:
        assert hasattr(RunScopedCache, name), name
