"""
T7 (docs/ml_strategy_plan.md §9) -- RM entitlement. Without this, the FDM
worklist had no relationship_manager_id at all and could not be routed
or access-scoped to an RM -- a real, disclosed blocker for any bank
pilot (docs/gap_analysis.md). The fix must satisfy three things at once:
present for FDM, absent-not-crashing for legacy, and stable (never
perturb any other field on regeneration since it consumes no rng draw).
"""

from __future__ import annotations

import os
import re
from datetime import date, timedelta

import pytest

from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
LEGACY_DIR = os.path.join(REPO_ROOT, "data_generator", "output")

RM_ID_PATTERN = re.compile(r"^RM\d{3}$")


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_every_fdm_party_has_a_well_formed_rm_id():
    rt = build_runtime("fdm_local")
    canonical = CanonicalSource(rt.source, load_binding("fdm"))
    parties = canonical.read("Party", as_at=date(2025, 10, 4))
    assert not parties.empty
    assert "relationship_manager_id" in parties.columns
    assert parties["relationship_manager_id"].notna().all()
    assert parties["relationship_manager_id"].apply(lambda v: bool(RM_ID_PATTERN.match(v))).all()


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_rm_id_is_deterministic_from_party_id_not_random():
    """The whole point of deriving it from a hash instead of rng: two
    separate reads of the same party must always agree, and a party's RM
    must not change across its bi-temporal versions."""
    from data_generator.fdm.generate_fdm import _rm_id_for

    assert _rm_id_for("PRTY00000") == _rm_id_for("PRTY00000")
    rt = build_runtime("fdm_local")
    canonical = CanonicalSource(rt.source, load_binding("fdm"))
    versions = canonical.versions("Party", party_id="PRTY00000") \
        if canonical.available("Party") else None
    # Not every party has more than one bi-temporal version -- this just
    # confirms the ones that do keep the same RM across versions.
    if versions is not None and "relationship_manager_id" in versions.columns and len(versions) > 1:
        assert versions["relationship_manager_id"].nunique() == 1


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_the_worklist_carries_relationship_manager_id_end_to_end():
    from agents.orchestrator import evaluate_book
    from datainsights.correlation.dedupe import dedupe
    from datainsights.fdm_worklist import build_rm_worklist
    from external_events.exposure_qualifier import load_events

    rt = build_runtime("fdm_local")
    event = load_events(rt.event_source_path)[0]
    as_of = event.event_date + timedelta(days=90)
    ids = sorted(rt.source.party(as_of)["PRTY_ID"])[:20]
    evals = evaluate_book(ids, source=rt.source, rules=rt.rules, as_of=as_of, event=event)
    recs = dedupe([e.recommendation for e in evals if e.recommendation])
    worklist = build_rm_worklist(recs, rt.source, rt.rules, as_of)
    if worklist.empty:
        pytest.skip("no recommendations in this 20-client sample -- not what this test checks")
    assert "relationship_manager_id" in worklist.columns
    assert (worklist["relationship_manager_id"] != "").all()


@pytest.mark.skipif(not os.path.isdir(LEGACY_DIR), reason="legacy data not generated")
def test_legacy_schema_degrades_honestly_without_crashing():
    """Legacy's binding does not map relationship_manager_id (real data
    exists in clients.csv but isn't contracted yet, see
    config/bindings/legacy.yaml's disclosure comment) -- reading Party
    must still work, just without that column, never raise."""
    from datainsights.sources.offline_local import OfflineLocalSource

    canonical = CanonicalSource(
        OfflineLocalSource(LEGACY_DIR, os.path.join(REPO_ROOT, "config", "entities.yaml")),
        load_binding("legacy"),
    )
    party = canonical.read("Party")
    assert not party.empty
    assert "relationship_manager_id" not in party.columns
