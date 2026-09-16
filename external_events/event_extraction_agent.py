"""
Event Extraction Agent (docs/agentic_plan.md A1) -- turns unstructured
text (a news item, a regulatory notice, a tender-award announcement) into
a validated external_events.exposure_qualifier.ExogenousEvent.

This is the plan's one genuinely new agentic capability: everywhere else
in this repo the LLM narrates facts Python already computed. Here, the
LLM reads text a rule cannot parse and proposes a structured event --
but it still never decides anything about a client. Exposure
qualification (external_events/exposure_qualifier.py's qualifies())
stays deterministic and untouched; this module's only job is producing
the ExogenousEvent that function consumes, with a source-text citation
and a validation gate the LLM cannot talk its way past.

Every extraction either passes ALL of _validate() below and becomes an
ExogenousEvent, or is written nowhere near the events table -- degrading
to a "needs_review" result, never a guessed value. This mirrors
agents/domain_agent.py's validate-or-fallback discipline exactly, applied
to structured extraction instead of narration.

Local Ollama only, via agents/model_factory.get_model() -- no network
call beyond localhost. No FinCrime/PEP surface: BANNED_TERMS from
agents/domain_agent.py is re-used unchanged so this agent is held to the
identical boundary.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date

from pydantic import BaseModel

from agents.domain_agent import BANNED_TERMS
from external_events.exposure_qualifier import ExogenousEvent

# Real Eurostat NACE Rev.2 section letters this dataset's generator
# actually uses (data_generator/generate_data.py's SECTORS) -- reused,
# not reinvented, so an extracted sector can only ever be one
# exposure_qualifier.sector_match() can genuinely evaluate against.
ALLOWED_NACE_SECTIONS = {"A", "C", "D", "F", "G", "H", "I", "J", "L", "M", "O", "Q"}

# Types this pass knows how to qualify exposure for -- extraction is not
# limited to this list (a new type just means exposure_qualifier.py needs
# a matching rule before it can act, same as adding a detector).
KNOWN_EVENT_TYPES = {"public_tender_award"}


class ExtractedEventFields(BaseModel):
    event_type: str
    event_date: str  # ISO yyyy-mm-dd, validated below -- not trusted as a date type from the model
    affected_country: str  # ISO 3166-1 alpha-2
    affected_sector: str  # single NACE Rev.2 section letter
    severity: int  # 1-5
    estimated_value_eur: float
    quote: str  # the exact sentence(s) from the source text the value/date came from
    confidence: float  # 0-1, the model's own stated confidence


@dataclass
class ExtractionResult:
    source_name: str
    source_ref: str
    event: ExogenousEvent | None
    quote: str | None
    confidence: float | None
    status: str  # "extracted" | "needs_review"
    problems: list[str] = field(default_factory=list)
    latency_seconds: float = 0.0


def _validate(raw: ExtractedEventFields, source_text: str, ingest_date: date) -> list[str]:
    """Every check here runs regardless of what the model claims -- this
    is the gate an extraction cannot talk its way past. Mirrors
    agents/domain_agent.py's _validate but for structured fields instead
    of narrative prose."""
    problems = []

    if raw.event_type not in KNOWN_EVENT_TYPES:
        problems.append(f"unknown event_type {raw.event_type!r} -- no exposure rule exists for it yet")

    try:
        parsed_date = date.fromisoformat(raw.event_date)
    except ValueError:
        problems.append(f"event_date {raw.event_date!r} is not a valid ISO date")
    else:
        # As-of correctness applies to ingestion too: an event dated after
        # the point we're ingesting at is either a data error or a leak,
        # never something to silently accept.
        if parsed_date > ingest_date:
            problems.append(f"event_date {parsed_date} is after ingest_date {ingest_date} (future event)")

    if not re.fullmatch(r"[A-Z]{2}", raw.affected_country):
        problems.append(f"affected_country {raw.affected_country!r} is not a 2-letter ISO code")

    if raw.affected_sector not in ALLOWED_NACE_SECTIONS:
        problems.append(f"affected_sector {raw.affected_sector!r} not in the known NACE section set "
                        f"{sorted(ALLOWED_NACE_SECTIONS)}")

    if not (1 <= raw.severity <= 5):
        problems.append(f"severity {raw.severity} outside 1..5")

    if raw.estimated_value_eur <= 0:
        problems.append(f"estimated_value_eur {raw.estimated_value_eur} must be positive")

    # Grounding: the quote must actually appear in the source text --
    # otherwise the "citation" is itself hallucinated, which is worse
    # than no citation.
    if raw.quote.strip() and raw.quote.strip() not in source_text:
        problems.append("quote is not a substring of the source text (ungrounded citation)")

    # The value claimed must be traceable to a number actually present in
    # the quote, AND that number must be tagged EUR (or untagged) rather
    # than another currency -- same discipline as domain_agent.py's
    # numeric-tolerance + _currency_problem checks, applied to extraction.
    #
    # A live run against a GBP notice showed why "EUR appears somewhere in
    # the quote" is not enough: the quote was "...no EUR figure given" --
    # a NEGATION, 60+ chars from the actual number, which a bare substring
    # check happily matched. So instead: find the currency marker (if any)
    # within a short window immediately around EACH number, and only treat
    # a number as EUR-grounded if its own nearby marker is EUR/€/absent --
    # never if a different currency sits right next to it.
    NON_EUR_MARKERS = ("gbp", "usd", "chf", "jpy", "£", "$")
    WINDOW = 12  # chars either side -- enough for "GBP " or " million EUR", not enough to cross sentences
    quote_numbers = []
    for m in re.finditer(r"[\d,]+\.?\d*", raw.quote):
        n_str = m.group()
        if not n_str.replace(",", "").replace(".", "").isdigit():
            continue
        n = float(n_str.replace(",", ""))
        window = raw.quote[max(0, m.start() - WINDOW):m.end() + WINDOW].lower()
        tagged_other_currency = any(marker in window for marker in NON_EUR_MARKERS)
        quote_numbers.append((n, tagged_other_currency))

    matching_eur_tagged = [
        n for n, tagged_other in quote_numbers if not tagged_other and (
            abs(raw.estimated_value_eur - n) / max(n, 1) < 0.01
            or abs(raw.estimated_value_eur - n * 1_000_000) / max(raw.estimated_value_eur, 1) < 0.01
            or abs(raw.estimated_value_eur - n * 1_000) / max(raw.estimated_value_eur, 1) < 0.01
        )
    ]
    matching_other_currency = [
        n for n, tagged_other in quote_numbers if tagged_other and (
            abs(raw.estimated_value_eur - n) / max(n, 1) < 0.01
            or abs(raw.estimated_value_eur - n * 1_000_000) / max(raw.estimated_value_eur, 1) < 0.01
        )
    ]
    if quote_numbers and not matching_eur_tagged and matching_other_currency:
        problems.append(f"estimated_value_eur {raw.estimated_value_eur} matches a number in the quote "
                        f"that is tagged with a non-EUR currency, not EUR (currency mismatch): "
                        f"{raw.quote!r}")
    elif quote_numbers and not matching_eur_tagged and not matching_other_currency:
        problems.append(f"estimated_value_eur {raw.estimated_value_eur} not traceable to any number "
                        f"in quote {raw.quote!r}")

    text_blob = f"{raw.quote} {raw.event_type}".lower()
    for banned in BANNED_TERMS:
        if banned in text_blob:
            problems.append(f"extraction touched a disallowed term: {banned!r}")

    return problems


def extract_event(text: str, *, source_name: str, source_ref: str, model,
                   ingest_date: date | None = None) -> ExtractionResult:
    """Local-Ollama structured extraction, validated before it is ever
    treated as a real event. Never raises -- any model/parse failure
    degrades to status='needs_review', exactly like DomainAgent.evaluate()
    degrades to a template rather than an unvalidated claim."""
    ingest_date = ingest_date or date.today()
    t0 = time.monotonic()
    try:
        from strands import Agent

        agent = Agent(
            model=model, tools=[],
            system_prompt=(
                "You extract ONE structured external event from a short news/notice "
                "text for a commercial bank's internal signal pipeline. Rules:\n"
                "1. event_type MUST be copied EXACTLY, character-for-character, from "
                f"this fixed list -- never invent your own wording: {sorted(KNOWN_EVENT_TYPES)}. "
                "If the text describes a tender being won or awarded, the value is "
                "always exactly the string 'public_tender_award', not 'tender', "
                "'tender_award', 'contract_award', or any other variant.\n"
                "2. event_date must be ISO yyyy-mm-dd.\n"
                "3. affected_country is a 2-letter ISO country code.\n"
                "4. affected_sector is a single NACE Rev.2 section letter "
                f"(one of {sorted(ALLOWED_NACE_SECTIONS)}).\n"
                "5. quote MUST be copied VERBATIM from the source text -- the exact "
                "sentence(s) that state the value and date. Never paraphrase it.\n"
                "6. estimated_value_eur must be a number that literally appears in "
                "quote (in EUR, or converted from millions/thousands stated there). "
                "If no value is stated in EUR (or convertible), use 0.\n"
                "7. severity is 1 (minor) to 5 (major), your own judgement of scale.\n"
                "8. confidence is your own 0..1 estimate of extraction correctness.\n"
                "9. Never mention financial crime, sanctions, PEP status, or "
                "politically exposed persons -- out of scope, handled elsewhere.\n"
                "If the text does not describe a recognised event type above with a "
                "date, country, sector and EUR value, still fill every field with "
                "your best read (event_type from the fixed list even if it's a weak "
                "match) and set confidence low -- validation downstream will catch it."
            ),
        )
        result = agent(f"Extract the event from this text:\n\n{text}",
                       structured_output_model=ExtractedEventFields)
        raw = result.structured_output
        if raw is None:
            return ExtractionResult(source_name, source_ref, None, None, None, "needs_review",
                                    ["agent returned no structured output"], time.monotonic() - t0)
    except Exception as e:  # noqa: BLE001 -- any agent/model failure -> needs_review, never raises
        return ExtractionResult(source_name, source_ref, None, None, None, "needs_review",
                                [f"{type(e).__name__}: {e}"], time.monotonic() - t0)

    problems = _validate(raw, text, ingest_date)
    latency = time.monotonic() - t0
    if problems:
        return ExtractionResult(source_name, source_ref, None, raw.quote, raw.confidence,
                                "needs_review", problems, latency)

    event = ExogenousEvent(
        event_id=str(uuid.uuid4()), event_date=date.fromisoformat(raw.event_date),
        event_type=raw.event_type, affected_country=raw.affected_country,
        affected_sector=raw.affected_sector, severity=raw.severity,
        estimated_value_eur=raw.estimated_value_eur,
    )
    return ExtractionResult(source_name, source_ref, event, raw.quote, raw.confidence,
                            "extracted", [], latency)
