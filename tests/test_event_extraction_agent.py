"""
M8/A1 + Phase 2 (docs/generalization_plan.md R2) verification for
external_events/event_extraction_agent.py.

Three tiers:
  - Deterministic validation (_validate) -- always runs, no Ollama
    needed. This is the gate the LLM cannot talk its way past, so it's
    tested exhaustively regardless of model availability. Run against
    BOTH registered types (public_tender_award, fx_rate_move) to prove
    _validate() is genuinely schema-driven, not tender-shaped code that
    happens to also accept fx fields.
  - Fallback discipline (classification/extraction model failure) --
    no Ollama needed.
  - A live-Ollama precision/recall run against a golden set spanning
    both event types (NOT generator output, NOT
    protected_evaluator_only/ -- authored for this test, disclosed as
    synthetic) -- skipped if Ollama isn't reachable, exactly like
    test_domain_agent.py's own live test.
"""

from __future__ import annotations

import urllib.request
from datetime import date

import pytest

from external_events.event_extraction_agent import (
    KNOWN_EVENT_TYPES,
    _build_fields_model,
    _classify_event_type,
    _validate,
    extract_event,
)
from tests._stub_model import FailingModel

TENDER_SOURCE_TEXT = "A public infrastructure tender worth EUR 3.2 million was awarded in Spain."
FX_SOURCE_TEXT = "The EUR/USD pair fell 6% against the dollar over the past month."


def tender_fields(**overrides):
    defaults = dict(
        event_type="public_tender_award", event_date="2026-06-14", affected_country="ES",
        affected_sector="C", severity=4, estimated_value_eur=3_200_000.0,
        quote=TENDER_SOURCE_TEXT, confidence=0.9,
    )
    defaults.update(overrides)
    return _build_fields_model("public_tender_award")(**defaults)


def fx_fields(**overrides):
    defaults = dict(
        event_type="fx_rate_move", event_date="2026-06-14", affected_country="",
        severity=3, currency_pair="EUR/USD", currency="USD", pct_change=-0.06, window_days=30,
        quote=FX_SOURCE_TEXT, confidence=0.85,
    )
    defaults.update(overrides)
    return _build_fields_model("fx_rate_move")(**defaults)


# --- registry-driven, both event types --------------------------------------

def test_both_shipped_types_are_extraction_eligible():
    assert {"public_tender_award", "fx_rate_move"} <= KNOWN_EVENT_TYPES


def test_valid_tender_extraction_passes():
    assert _validate(tender_fields(), TENDER_SOURCE_TEXT, date(2026, 6, 20), "public_tender_award") == []


def test_valid_fx_extraction_passes():
    assert _validate(fx_fields(), FX_SOURCE_TEXT, date(2026, 6, 20), "fx_rate_move") == []


# --- deterministic validation: field-format checks, driven by the registry --

def test_future_event_date_rejected():
    problems = _validate(tender_fields(event_date="2099-01-01"), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert any("future" in p for p in problems)


def test_malformed_date_rejected():
    problems = _validate(tender_fields(event_date="not-a-date"), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert any("not a valid ISO date" in p for p in problems)


def test_bad_country_code_rejected():
    problems = _validate(tender_fields(affected_country="Spain"), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert any("does not match required pattern" in p for p in problems)


def test_unknown_nace_sector_rejected():
    problems = _validate(tender_fields(affected_sector="Z"), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert any("not in" in p for p in problems)


def test_severity_out_of_range_rejected():
    problems = _validate(tender_fields(severity=9), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert any("above maximum" in p for p in problems)


def test_non_positive_value_rejected():
    problems = _validate(tender_fields(estimated_value_eur=0), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert any("must be greater than" in p for p in problems)


def test_ungrounded_quote_rejected():
    """The quote must actually appear in the source text -- a fabricated
    citation is worse than none."""
    problems = _validate(tender_fields(quote="This sentence was never in the source."),
                         TENDER_SOURCE_TEXT, date(2026, 6, 20), "public_tender_award")
    assert any("not a substring" in p for p in problems)


def test_value_untraceable_to_quote_rejected():
    """A value that doesn't correspond to any number actually stated in
    the quote is a hallucinated figure."""
    problems = _validate(tender_fields(estimated_value_eur=99_000_000.0), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert any("not traceable" in p for p in problems)


def test_value_in_millions_notation_accepted():
    """'EUR 3.2 million' in the quote must satisfy a 3_200_000 value --
    the check must understand million/thousand notation, not just
    literal digit matches."""
    problems = _validate(tender_fields(estimated_value_eur=3_200_000.0), TENDER_SOURCE_TEXT,
                         date(2026, 6, 20), "public_tender_award")
    assert problems == []


def test_non_eur_currency_claimed_as_eur_rejected():
    """A live Ollama run once did exactly this -- reported a GBP figure
    as estimated_value_eur with no EUR marker anywhere in the quote."""
    gbp_quote = "A GBP 2,000,000 tender was awarded in the United Kingdom construction sector."
    problems = _validate(tender_fields(quote=gbp_quote, estimated_value_eur=2_000_000.0),
                         gbp_quote, date(2026, 6, 20), "public_tender_award")
    assert any("currency mismatch" in p for p in problems)


def test_banned_fincrime_term_rejected():
    banned_text = TENDER_SOURCE_TEXT + " Linked to sanctions screening."
    problems = _validate(tender_fields(quote=banned_text), banned_text,
                         date(2026, 6, 20), "public_tender_award")
    assert any("disallowed term" in p for p in problems)


# --- fx_rate_move's own fields, proving _validate() isn't tender-shaped ----

def test_fx_bad_currency_pair_pattern_rejected():
    problems = _validate(fx_fields(currency_pair="eurusd"), FX_SOURCE_TEXT,
                         date(2026, 6, 20), "fx_rate_move")
    assert any("does not match required pattern" in p for p in problems)


def test_fx_percent_change_grounded_in_quote_as_a_percentage():
    """The quote says '6%', not '0.06' -- pct_change is stored as a
    fraction (-0.06), so the grounding check must understand
    percent-as-fraction notation, not just literal/thousand/million
    scaling."""
    assert _validate(fx_fields(pct_change=-0.06), FX_SOURCE_TEXT, date(2026, 6, 20), "fx_rate_move") == []


def test_fx_percent_change_untraceable_to_quote_rejected():
    problems = _validate(fx_fields(pct_change=-0.40), FX_SOURCE_TEXT, date(2026, 6, 20), "fx_rate_move")
    assert any("not traceable" in p for p in problems)


def test_fx_optional_affected_country_empty_is_fine():
    assert _validate(fx_fields(affected_country=""), FX_SOURCE_TEXT, date(2026, 6, 20), "fx_rate_move") == []


def test_fx_affected_country_when_present_still_validated():
    problems = _validate(fx_fields(affected_country="Spain"), FX_SOURCE_TEXT,
                         date(2026, 6, 20), "fx_rate_move")
    assert any("does not match required pattern" in p for p in problems)


# --- fallback discipline (no Ollama needed) -------------------------------

def test_model_failure_degrades_to_needs_review_never_raises():
    result = extract_event("Some notice text.", source_name="test", source_ref="t1",
                           model=FailingModel(), ingest_date=date(2026, 6, 20))
    assert result.status == "needs_review"
    assert result.event is None
    assert result.problems


def test_classify_stage_failure_propagates_so_extract_event_can_catch_it():
    """_classify_event_type itself raises on a model failure (extract_event
    is what converts that into needs_review, tested above) -- this proves
    the raise happens at the classify stage, not swallowed silently."""
    with pytest.raises(RuntimeError):
        _classify_event_type("irrelevant text", FailingModel())


# --- Opt-in live tests: real local Ollama ----------------------------------

def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_classification_picks_the_right_type_for_each_source_text():
    from agents.model_factory import ModelConfig, get_model

    model = get_model(ModelConfig(mode="local"))
    tender_type, _ = _classify_event_type(TENDER_SOURCE_TEXT, model)
    fx_type, _ = _classify_event_type(FX_SOURCE_TEXT, model)
    print(f"\nClassification: tender_text->{tender_type!r}  fx_text->{fx_type!r}")
    assert tender_type == "public_tender_award"
    assert fx_type == "fx_rate_move"


# Hand-written for this test -- NOT generator output, NOT
# protected_evaluator_only/. Spans both registered event types. 8 should
# extract cleanly; 6 are deliberately awkward (wrong-shaped date, no
# value, non-EUR currency the model must still report faithfully, a
# sector outside the known set, irrelevant text) -- an extraction agent
# that can't be wrong on anything is an untested one.
GOLDEN_NOTICES = [
    ("tender_es_manufacturing",
     "TED Notice 2026/S 112-045123: A public infrastructure tender worth "
     "EUR 3,200,000 was awarded on 14 June 2026 to a manufacturing "
     "contractor (NACE section C) operating in Spain (ES).",
     True, "public_tender_award"),
    ("tender_fr_construction",
     "Contract award notice, France (FR): a EUR 1.8 million public works "
     "contract in the construction sector (NACE F) was signed on 3 March 2026.",
     True, "public_tender_award"),
    ("tender_de_logistics",
     "Germany (DE) transport authority awarded a EUR 4,500,000 logistics "
     "framework contract (NACE section H) on 20 January 2026.",
     True, "public_tender_award"),
    ("tender_it_hospitality",
     "Italy (IT): a EUR 900,000 tender for hospitality services (NACE "
     "section I) was awarded on 5 May 2026.",
     True, "public_tender_award"),
    ("tender_nl_energy",
     "Netherlands (NL) awarded a EUR 6,100,000 renewable energy "
     "infrastructure tender (NACE section D) on 11 February 2026.",
     True, "public_tender_award"),
    ("tender_pl_agri",
     "Poland (PL): EUR 750,000 agricultural infrastructure tender (NACE "
     "section A) awarded 28 April 2026.",
     True, "public_tender_award"),
    ("fx_eurusd_drop",
     "The EUR/USD pair fell 6% against the dollar over the past month, "
     "one of the sharpest moves this year, market analysts noted on 10 June 2026.",
     True, "fx_rate_move"),
    ("fx_eurgbp_rise",
     "Sterling strengthened, with EUR/GBP rising 4% over the past 30 days "
     "as reported on 2 April 2026.",
     True, "fx_rate_move"),
    ("no_value_stated",
     "A public tender was awarded in the construction sector in France. "
     "No financial details were disclosed in this notice.",
     False, None),
    ("non_eur_currency",
     "A GBP 2,000,000 tender was awarded in the United Kingdom construction "
     "sector on 1 June 2026 -- no EUR figure given.",
     False, None),
    ("unsupported_sector",
     "A EUR 5,000,000 tender in the FinCrime-adjacent sanctions-screening "
     "software sector was awarded in Spain on 1 June 2026.",
     False, None),  # both: unknown sector AND a banned term -- must be rejected
    ("irrelevant_text",
     "The company announced its quarterly earnings call schedule for "
     "next month; no tender or award is mentioned.",
     False, None),
]


@pytest.mark.skipif(not _ollama_reachable(), reason="local Ollama server not reachable at localhost:11434")
def test_live_extraction_precision_recall_on_golden_notices():
    from agents.model_factory import ModelConfig, get_model

    model = get_model(ModelConfig(mode="local"))
    ingest_date = date(2026, 12, 31)

    results = []
    for name, text, should_extract, _expected_type in GOLDEN_NOTICES:
        result = extract_event(text, source_name="golden_test", source_ref=name,
                               model=model, ingest_date=ingest_date)
        results.append((name, should_extract, result))

    tp = sum(1 for _, expected, r in results if expected and r.status == "extracted")
    fp = sum(1 for _, expected, r in results if not expected and r.status == "extracted")
    fn = sum(1 for _, expected, r in results if expected and r.status != "extracted")
    tn = sum(1 for _, expected, r in results if not expected and r.status != "extracted")

    print(f"\nEvent extraction golden set: TP={tp} FP={fp} FN={fn} TN={tn} (of {len(results)})")
    for name, expected, r in results:
        event_type = r.event.event_type if r.event else None
        print(f"  {name}: expected_extract={expected} got={r.status} event_type={event_type} problems={r.problems}")

    # Not asserting a perfect score -- a 7B local model on hand-written
    # prose will make mistakes. Asserting the discipline that matters:
    # nothing structurally wrong ever reaches "extracted" (0 false
    # positives is the hard requirement; missed real events are a
    # precision/recall number to track, not a boundary violation).
    assert fp == 0, f"a should-NOT-extract notice was extracted: {[n for n, e, r in results if not e and r.status == 'extracted']}"
