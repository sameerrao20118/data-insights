"""
R13 (docs/refactor_plan.md §6b) -- the whole-book path shares ONE
CanonicalSource whose per-(concept, as_at) frame cache and party/account
position index make each client's reads O(its own rows), not O(the
book). The acceptance the plan demanded and M16 could not give: golden
equivalence against the per-client path on a window WITH positive
detections, plus proof the cache can't be corrupted by a caller.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pandas as pd
import pytest

from agents.orchestrator import evaluate_book, evaluate_client
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from external_events.exposure_qualifier import load_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
EVENTS = os.path.join(REPO_ROOT, "external_events", "output_fdm", "tender_events.csv")

pytestmark = pytest.mark.skipif(not (os.path.isdir(FDM_DIR) and os.path.exists(EVENTS)),
                                reason="FDM data not generated")

REC_FIELDS = ("nba_category", "hypothesis", "recommended_action", "sized_offer_eur", "sizing_basis",
              "signal_strength", "endogenous_signal_type", "exogenous_event_type", "confirming_domains")


def _book():
    rt = build_runtime("fdm_local")
    event = load_events(EVENTS)[0]
    as_of = event.event_date + timedelta(days=90)  # the demo's own window -- it has 36 positives
    parties = CanonicalSource(rt.source, load_binding("fdm")).read("Party", as_at=as_of)
    return rt, event, as_of, sorted(parties["party_id"])


def _rec_tuple(ev):
    r = ev.recommendation
    if r is None:
        return None
    return tuple(getattr(r, f) for f in REC_FIELDS)


def test_shared_canonical_book_is_golden_equivalent_to_per_client_path_on_a_positive_window():
    rt, event, as_of, prty_ids = _book()
    shared = evaluate_book(prty_ids, source=rt.source, rules=rt.rules, as_of=as_of, event=event)
    # The pre-R13 path: a fresh CanonicalSource per client (evaluate_client's
    # default when no `canonical` is passed) -- byte-for-byte the old code.
    per_client = [evaluate_client(p, source=rt.source, rules=rt.rules, as_of=as_of, event=event, narrate=False)
                  for p in prty_ids]

    assert [e.prty_id for e in shared] == [e.prty_id for e in per_client]
    assert all(e.error is None for e in shared)
    n_positive = sum(1 for e in shared if e.recommendation is not None)
    assert n_positive > 0, "the golden window must contain positive detections or the test proves nothing"
    for a, b in zip(shared, per_client):
        assert _rec_tuple(a) == _rec_tuple(b), a.prty_id
        assert [(s.signal_type, s.magnitude, s.evidence_ref) for s in a.signals] == \
               [(s.signal_type, s.magnitude, s.evidence_ref) for s in b.signals], a.prty_id


def test_cached_frames_cannot_be_corrupted_by_a_caller():
    """pandas >= 3 is copy-on-write; this pins that the cache relies on it
    correctly -- a tool assigning a column on a returned frame (as
    agents/tools.py does) must never leak into the next client's read."""
    rt, _, as_of, prty_ids = _book()
    canonical = CanonicalSource(rt.source, load_binding("fdm"))
    whole = canonical.read("Account", as_at=as_of)
    whole["party_id"] = "CORRUPTED"
    whole.loc[:, "product_class"] = "junk"
    again = canonical.read("Account", as_at=as_of)
    assert (again["party_id"] != "CORRUPTED").all()
    assert (again["product_class"] != "junk").all()

    one = canonical.read("Account", as_at=as_of, party_id=prty_ids[0])
    one["original_limit"] = -1
    assert (canonical.read("Account", as_at=as_of, party_id=prty_ids[0])["original_limit"] != -1).all()


def test_index_slice_matches_a_plain_mask_including_absent_keys():
    rt, _, as_of, prty_ids = _book()
    canonical = CanonicalSource(rt.source, load_binding("fdm"))
    whole = canonical.read("BalanceObservation")
    acct = whole["account_id"].iloc[0]
    expected = whole[whole["account_id"] == acct].reset_index(drop=True)
    pd.testing.assert_frame_equal(canonical.read("BalanceObservation", account_id=acct), expected)
    assert canonical.read("BalanceObservation", account_id="NO_SUCH_ACCOUNT").empty
    assert canonical.read("Account", as_at=as_of, party_id="NO_SUCH_PARTY").empty
    # party then account narrowing (the fallback path inside _slice)
    p = prty_ids[0]
    acc = canonical.read("Account", as_at=as_of, party_id=p)
    if not acc.empty:
        a = acc["account_id"].iloc[0]
        both = canonical.read("Account", as_at=as_of, party_id=p, account_id=a)
        assert set(both["account_id"]) == {a} and set(both["party_id"]) == {p}
