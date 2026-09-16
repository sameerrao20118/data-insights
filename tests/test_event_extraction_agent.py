"""
M8/A1 verification for external_events/event_extraction_agent.py.

Two tiers, matching agents/domain_agent.py's own test split:
  - Deterministic validation (_validate) -- always runs, no Ollama needed.
    This is the gate the LLM cannot talk its way past, so it's tested
    exhaustively regardless of model availability.
  - A live-Ollama precision/recall run against 10 hand-written notices
    (NOT generator output, NOT protected_evaluator_only/ -- authored for
    this test, disclosed as synthetic) -- skipped if Ollama isn't
    reachable, exactly like test_domain_agent.py's own live test.
"""

from __future__ import annotations

import urllib.request
from datetime import date

import pytest

from external_events.event_extraction_agent import (
    ExtractedEventFields,
    _validate,
    extract_event,
)
from tests._stub_model import FailingModel


def fields(**overrides):
    defaults = dict(
        event_type="public_tender_award", event_date="2026-06-14", affected_country="ES",
        affected_sector="C", severity=4, estimated_value_eur=3_200_000.0,
        quote="A public infrastructure tender worth EUR 3.2 million was awarded in Spain.",
        confidence=0.9,
    )
    defaults.update(overrides)
    return ExtractedEventFields(**defaults)


SOURCE_TEXT = "A public infrastructure tender worth EUR 3.2 million was awarded in Spain."


# --- deterministic validation --------------------------------------------

def test_valid_extraction_passes():
    assert _validate(fields(), SOURCE_TEXT, date(2026, 6, 20)) == []


def test_future_event_date_rejected():
    problems = _validate(fields(event_date="2099-01-01"), SOURCE_TEXT, date(2026, 6, 20))
    assert any("future" in p for p in problems)


def test_malformed_date_rejected():
    problems = _validate(fields(event_date="not-a-date"), SOURCE_TEXT, date(2026, 6, 20))
    assert any("not a valid ISO date" in p for p in problems)


def test_bad_country_code_rejected():
    problems = _validate(fields(affected_country="Spain"), SOURCE_TEXT, date(2026, 6, 20))
    assert any("not a 2-letter ISO code" in p for p in problems)


def test_unknown_nace_sector_rejected():
    problems = _validate(fields(affected_sector="Z"), SOURCE_TEXT, date(2026, 6, 20))
    assert any("not in the known NACE section set" in p for p in problems)


def test_severity_out_of_range_rejected():
    problems = _validate(fields(severity=9), SOURCE_TEXT, date(2026, 6, 20))
    assert any("outside 1..5" in p for p in problems)


def test_non_positive_value_rejected():
    problems = _validate(fields(estimated_value_eur=0), SOURCE_TEXT, date(2026, 6, 20))
    assert any("must be positive" in p for p in problems)


def test_ungrounded_quote_rejected():
    """The quote must actually appear in the source text -- a fabricated
    citation is worse than none."""
    problems = _validate(fields(quote="This sentence was never in the source."),
                         SOURCE_TEXT, date(2026, 6, 20))
    assert any("not a substring" in p for p in problems)


def test_value_untraceable_to_quote_rejected():
    """A value that doesn't correspond to any number actually stated in
    the quote is a hallucinated figure."""
    problems = _validate(fields(estimated_value_eur=99_000_000.0), SOURCE_TEXT, date(2026, 6, 20))
    assert any("not traceable" in p for p in problems)


def test_value_in_millions_notation_accepted():
    """'EUR 3.2 million' in the quote must satisfy a 3_200_000 value --
    the check must understand million/thousand notation, not just
    literal digit matches."""
    problems = _validate(fields(estimated_value_eur=3_200_000.0), SOURCE_TEXT, date(2026, 6, 20))
    assert problems == []


def test_non_eur_currency_claimed_as_eur_rejected():
    """A live Ollama run once did exactly this -- reported a GBP figure
    as estimated_value_eur with no EUR marker anywhere in the quote."""
    gbp_quote = "A GBP 2,000,000 tender was awarded in the United Kingdom construction sector."
    problems = _validate(fields(quote=gbp_quote, estimated_value_eur=2_000_000.0),
                         gbp_quote, date(2026, 6, 20))
    assert any("currency mismatch" in p for p in problems)


def test_unknown_event_type_rejected():
    problems = _validate(fields(event_type="merger_announcement"), SOURCE_TEXT, date(2026, 6, 20))
    assert any("no exposure rule exists" in p for p in problems)


def test_banned_fincrime_term_rejected():
    problems = _validate(fields(quote=SOURCE_TEXT + " Linked to sanctions screening."),
                         SOURCE_TEXT + " Linked to sanctions screening.", date(2026, 6, 20))
    assert any("disallowed term" in p for p in problems)


# --- fallback discipline (no Ollama needed) -------------------------------

def test_model_failure_degrades_to_needs_review_never_raises():
    result = extract_event("Some notice text.", source_name="test", source_ref="t1",
                           model=FailingModel(), ingest_date=date(2026, 6, 20))
    assert result.status == "needs_review"
    assert result.event is None
    assert result.problems


# --- Opt-in live test: real local Ollama, precision/recall on 10 notices --

def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


# Hand-written for this test -- NOT generator output, NOT
# protected_evaluator_only/. 6 should extract cleanly; 4 are deliberately
# awkward (wrong-shaped date, no value, non-EUR currency the model must
# still report faithfully, a sector outside the known set) -- an
# extraction agent that can't be wrong on anything is an untested one.
GOLDEN_NOTICES = [
    ("tender_es_manufacturing",
     "TED Notice 2026/S 112-045123: A public infrastructure tender worth "
     "EUR 3,200,000 was awarded on 14 June 2026 to a manufacturing "
     "contractor (NACE section C) operating in Spain (ES).",
     True),
    ("tender_fr_construction",
     "Contract award notice, France (FR): a EUR 1.8 million public works "
     "contract in the construction sector (NACE F) was signed on 3 March 2026.",
     True),
    ("tender_de_logistics",
     "Germany (DE) transport authority awarded a EUR 4,500,000 logistics "
     "framework contract (NACE section H) on 20 January 2026.",
     True),
    ("tender_it_hospitality",
     "Italy (IT): a EUR 900,000 tender for hospitality services (NACE "
     "section I) was awarded on 5 May 2026.",
     True),
    ("tender_nl_energy",
     "Netherlands (NL) awarded a EUR 6,100,000 renewable energy "
     "infrastructure tender (NACE section D) on 11 February 2026.",
     True),
    ("tender_pl_agri",
     "Poland (PL): EUR 750,000 agricultural infrastructure tender (NACE "
     "section A) awarded 28 April 2026.",
     True),
    ("no_value_stated",
     "A public tender was awarded in the construction sector in France. "
     "No financial details were disclosed in this notice.",
     False),
    ("non_eur_currency",
     "A GBP 2,000,000 tender was awarded in the United Kingdom construction "
     "sector on 1 June 2026 -- no EUR figure given.",
     False),
    ("unsupported_sector",
     "A EUR 5,000,000 tender in the FinCrime-adjacent sanctions-screening "
     "software sector was awarded in Spain on 1 June 2026.",
     False),  # both: unknown sector AND a banned term -- must be rejected
    ("irrelevant_text",
     "The company announced its quarterly earnings call schedule for "
     "next month; no tender or award is mentioned.",
     False),
]


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_extraction_precision_recall_on_golden_notices():
    from agents.model_factory import ModelConfig, get_model

    model = get_model(ModelConfig(mode="local", model_id="qwen2.5:7b"))
    ingest_date = date(2026, 12, 31)

    results = []
    for name, text, should_extract in GOLDEN_NOTICES:
        result = extract_event(text, source_name="golden_test", source_ref=name,
                               model=model, ingest_date=ingest_date)
        results.append((name, should_extract, result))

    tp = sum(1 for _, expected, r in results if expected and r.status == "extracted")
    fp = sum(1 for _, expected, r in results if not expected and r.status == "extracted")
    fn = sum(1 for _, expected, r in results if expected and r.status != "extracted")
    tn = sum(1 for _, expected, r in results if not expected and r.status != "extracted")

    print(f"\nEvent extraction golden set: TP={tp} FP={fp} FN={fn} TN={tn} (of {len(results)})")
    for name, expected, r in results:
        print(f"  {name}: expected_extract={expected} got={r.status} problems={r.problems}")

    # Not asserting a perfect score -- a 7B local model on hand-written
    # prose will make mistakes. Asserting the discipline that matters:
    # nothing structurally wrong ever reaches "extracted" (0 false
    # positives is the hard requirement; missed real events are a
    # precision/recall number to track, not a boundary violation).
    assert fp == 0, f"a should-NOT-extract notice was extracted: {[n for n, e, r in results if not e and r.status == 'extracted']}"
