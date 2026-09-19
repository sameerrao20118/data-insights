"""
R17 (docs/refactor_plan.md §6f): nothing downstream of a detector assumes
EUR. The account's currency rides on the tool evidence, the Signal and
the Recommendation; sizing floors and offers are in that currency; the
narrator is told the real currency and is caught naming the wrong one.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import pytest

from agents.domain_agent import _currency_problem, describe_evidence
from datainsights.correlation.hypothesis import EndogenousSizing, assemble
from detection_engine import cash_buildup
from detection_engine.signal import Signal

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sig(currency, magnitude=3.0):
    return Signal(prty_id="P1", signal_type="cash_buildup", domain="deposits", direction="increase",
                  magnitude=magnitude, observed_date=date(2024, 6, 1), evidence_ref="TEST",
                  source_tables=("TEST",), raw_measure={"current_balance": 500_000.0, "prior_balance": 100_000.0,
                                                        "currency": currency})


def test_recommendation_and_offer_are_in_the_signals_currency():
    usd = assemble("P1", [_sig("USD")], as_of=date(2024, 6, 30), high_risk_flag=False)
    eur = assemble("P1", [_sig("EUR")], as_of=date(2024, 6, 30), high_risk_flag=False)
    assert usd.currency == "USD" and "USD 400,000" in usd.recommended_action and "EUR" not in usd.recommended_action
    assert eur.currency == "EUR" and "EUR 400,000" in eur.recommended_action


def test_min_offer_floor_is_per_currency():
    sizing = EndogenousSizing(min_offer_eur=10_000, min_offer_by_currency={"USD": 450_000})
    small = Signal(prty_id="P1", signal_type="cash_buildup", domain="deposits", direction="increase", magnitude=3,
                   observed_date=date(2024, 6, 1), evidence_ref="T", source_tables=("T",),
                   raw_measure={"current_balance": 500_000.0, "prior_balance": 100_000.0, "currency": "USD"})
    rec = assemble("P1", [small], as_of=date(2024, 6, 30), high_risk_flag=False, sizing=sizing)
    assert rec.sized_offer_eur is None  # 400k USD is below the USD floor of 450k
    assert sizing.min_offer("GBP") == 10_000  # unlisted currency -> scalar default


def test_cash_buildup_floor_is_per_currency_when_the_frame_carries_one():
    cfg = cash_buildup.DetectorConfig(window_days=30, min_increase_pct=0.10, min_prior_balance=5000,
                                      cooldown_days=0, rule_version="t", min_prior_balance_by_currency={"USD": 100_000})
    rows = []
    for d in range(0, 70, 5):
        rows.append({"party_id": "P1", "account_id": "A1", "observed_at": date(2024, 1, 1) + pd.Timedelta(days=d),
                     "balance": 20_000 + d * 1000, "currency": "USD"})
    df = pd.DataFrame(rows)
    out = cash_buildup.detect(df, cfg, "t")
    assert (out["status"] != "detected").all()  # prior balances (~20-60k USD) sit below the USD floor
    df["currency"] = "EUR"
    out = cash_buildup.detect(df, cfg, "t")
    assert (out["status"] == "detected").any()
    assert set(out["currency"]) == {"EUR"}


def test_fallback_sentences_use_the_evidence_currency_and_mismatch_is_caught_both_ways():
    e = {"status": "detected", "increase_pct": 0.5, "current_balance": 150_000.0, "prior_balance": 100_000.0,
         "event_date": "2024-06-01", "currency": "USD"}
    sentence = describe_evidence("check_cash_buildup", e)
    assert "USD 150,000" in sentence and "EUR" not in sentence
    assert _currency_problem("balance rose to EUR 150,000", {"check_cash_buildup": e}) is not None
    assert _currency_problem("balance rose to USD 150,000", {"check_cash_buildup": e}) is None
    e_eur = dict(e, currency="EUR")
    assert _currency_problem("balance rose to $150,000", {"check_cash_buildup": e_eur}) is not None


@pytest.mark.skipif(not os.path.isdir(os.path.join(REPO_ROOT, "data_generator", "output_fdm_sba")),
                    reason="SBA data not generated")
def test_sba_book_is_usd_end_to_end_and_the_worklist_says_so():
    from agents.orchestrator import evaluate_book
    from datainsights.fdm_worklist import build_rm_worklist
    from datainsights.runtime import build_runtime
    from datainsights.semantic.binding import load_binding
    from datainsights.semantic.canonical import CanonicalSource

    rt = build_runtime("sba_local")
    canonical = CanonicalSource(rt.source, load_binding("sba"))
    as_of = pd.to_datetime(canonical.read("BalanceObservation")["observed_at"]).max().date()
    ids = sorted(canonical.read("Party", as_at=as_of)["party_id"])[:120]
    book = evaluate_book(ids, source=rt.source, rules=rt.rules, as_of=as_of, binding_name="sba")
    recs = [e.recommendation for e in book if e.recommendation]
    assert recs and all(r.currency == "USD" for r in recs)
    assert not any("EUR" in r.recommended_action for r in recs)
    wl = build_rm_worklist(recs, rt.source, rt.rules, as_of, binding_name="sba")
    assert set(wl["currency"]) == {"USD"}
