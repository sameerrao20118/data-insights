"""
R23: large_incoming_payment ported into the canonical detector set --
same statistics as the retired legacy detector (median + k*MAD over
strictly-prior credits, per-currency floor, cooldown), canonical
columns, and a Signal like every other detector.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from detection_engine import large_incoming_payment as lip


def _cfg(**kw):
    base = dict(baseline_window_days=90, min_baseline_transactions=5, mad_multiplier=6.0, cooldown_days=30,
                rule_version="t", absolute_floor=1000.0, absolute_floor_by_currency={"JPY": 100_000})
    base.update(kw)
    return lip.DetectorConfig(**base)


def _credits(n=30, amount=1000.0, currency="EUR", start=date(2024, 1, 1)):
    rows = [{"party_id": "P1", "account_id": "A1", "posted_at": start + timedelta(days=3 * i), "amount": amount + (i % 3) * 10,
             "direction": "credit", "currency": currency, "transaction_id": f"T{i}"} for i in range(n)]
    return pd.DataFrame(rows)


def test_a_payment_far_above_the_clients_own_baseline_is_detected_and_becomes_a_signal():
    df = _credits()
    df.loc[len(df)] = {"party_id": "P1", "account_id": "A1", "posted_at": date(2024, 4, 15), "amount": 250_000.0,
                       "direction": "credit", "currency": "EUR", "transaction_id": "BIG"}
    out = lip.detect(df, _cfg(), "t")
    hit = out[out["status"] == "detected"]
    assert len(hit) == 1 and hit.iloc[0]["transaction_id"] == "BIG"
    sig = lip.to_signal(hit.iloc[0])
    assert sig.signal_type == "large_incoming_payment" and sig.domain == "deposits"
    assert sig.magnitude == 1.0 and sig.raw_measure["currency"] == "EUR"
    assert sig.evidence_ref.startswith("Transaction:A1:BIG:2024-04-15")


def test_baseline_uses_strictly_prior_credits_only_no_leakage():
    df = _credits(n=4)  # below min_baseline_transactions for the first rows
    df.loc[len(df)] = {"party_id": "P1", "account_id": "A1", "posted_at": date(2024, 1, 20), "amount": 99_999.0,
                       "direction": "credit", "currency": "EUR", "transaction_id": "EARLY"}
    out = lip.detect(df, _cfg(), "t")
    assert (out["status"] == "insufficient_evidence").all()  # nothing detected on a 4-credit history


def test_debits_and_sub_floor_amounts_are_never_flagged():
    df = _credits(amount=100.0)
    df.loc[len(df)] = {"party_id": "P1", "account_id": "A1", "posted_at": date(2024, 4, 15), "amount": 900.0,
                       "direction": "credit", "currency": "EUR", "transaction_id": "SMALL"}  # > baseline, < floor 1000
    df.loc[len(df)] = {"party_id": "P1", "account_id": "A1", "posted_at": date(2024, 4, 16), "amount": 1e6,
                       "direction": "debit", "currency": "EUR", "transaction_id": "DEBIT"}
    out = lip.detect(df, _cfg(), "t")
    assert not (out["status"] == "detected").any()


def test_floor_is_per_currency():
    df = _credits(amount=1000.0, currency="JPY")
    df.loc[len(df)] = {"party_id": "P1", "account_id": "A1", "posted_at": date(2024, 4, 15), "amount": 50_000.0,
                       "direction": "credit", "currency": "JPY", "transaction_id": "JP"}
    assert not (lip.detect(df, _cfg(), "t")["status"] == "detected").any()  # below the JPY floor
    df["currency"] = "EUR"
    assert (lip.detect(df, _cfg(), "t")["status"] == "detected").any()


def test_cooldown_suppresses_a_second_hit_within_the_window():
    df = _credits()
    for k, d in enumerate((date(2024, 4, 15), date(2024, 4, 20))):
        df.loc[len(df)] = {"party_id": "P1", "account_id": "A1", "posted_at": d, "amount": 250_000.0 + k,
                           "direction": "credit", "currency": "EUR", "transaction_id": f"BIG{k}"}
    out = lip.apply_cooldown(lip.detect(df, _cfg(), "t"), _cfg())
    assert list(out[out["transaction_id"].isin(["BIG0", "BIG1"])].sort_values("event_date")["status"]) == \
        ["detected", "suppressed_cooldown"]


def test_it_is_registered_as_a_deposits_tool_with_a_category_and_a_pack_entry():
    import agents.tools  # noqa: F401
    from agents.domain_registry import get
    from datainsights import domain_registry
    from datainsights.packs import load_pack
    assert "check_large_incoming_payment" in get("deposits").detector_by_tool
    assert domain_registry.category_for("large_incoming_payment") == "TREASURY_OPPORTUNITY"
    assert "large_incoming_payment" in load_pack("banking").detectors


import os  # noqa: E402

import pytest  # noqa: E402

LEGACY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_generator", "output")


@pytest.mark.skipif(not os.path.isdir(LEGACY_DIR), reason="legacy-schema data not generated")
def test_it_fires_on_the_legacy_book_through_the_one_pipeline():
    """The legacy generator injects outsized credits; the retired pipeline
    detected them and so must the one that replaced it (R23)."""
    from agents.orchestrator import evaluate_book
    from datainsights.runtime import build_runtime
    from datainsights.semantic.binding import load_binding
    from datainsights.semantic.canonical import CanonicalSource

    rt = build_runtime("legacy_local")
    c = CanonicalSource(rt.source, load_binding("legacy"))
    as_of = pd.to_datetime(c.read("Transaction")["posted_at"]).max().date()
    ids = sorted(c.read("Party", as_at=as_of)["party_id"])[:150]
    book = evaluate_book(ids, source=rt.source, rules=rt.rules, as_of=as_of, binding_name="legacy")
    assert all(e.error is None for e in book)
    hits = [s for e in book for s in e.signals if s.signal_type == "large_incoming_payment"]
    assert hits, "expected large_incoming_payment signals on the legacy book"
    assert all(s.raw_measure["currency"] and s.magnitude > 0 for s in hits)
