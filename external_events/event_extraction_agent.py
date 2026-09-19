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

docs/generalization_plan.md Phase 2 (R2), closing the gap this module's
docstring used to disclose: extraction is now driven entirely by each
event type's `extraction:`/`payload:` blocks in config/event_types.yaml
(via external_events/event_registry.py), not a schema hardcoded for
public_tender_award. Two LLM calls, not one:

  1. classify -- which registered type (if any) does this text describe?
     A small, cheap structured call; a type this pass doesn't know how
     to extract, or no confident match, exits straight to
     "needs_review" without spending a second call.
  2. extract -- a pydantic model built ON THE FLY from that type's own
     `extraction.core_fields` + `payload` schema (pydantic's
     `create_model`), with a system prompt generated from the same
     schema. Registering a new type with an `extraction:` block makes
     it extractable with zero changes here.

_validate()'s grounding check (quote substring, million/thousand/percent
notation, currency-marker matching -- see the real GBP-tagged-as-EUR bug
a live run once found, still guarded against below) is generalized to
run against ANY field a type marks `grounded_in_quote: true`, not just
public_tender_award's estimated_value_eur.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from pydantic import BaseModel, create_model
from strands import tool

from agents.domain_agent import BANNED_TERMS
from datainsights.prompts import load_prompt
from external_events import event_registry
from external_events.exposure_qualifier import ExogenousEvent

# Types this pass can actually extract -- any registered type that
# declares an `extraction:` block (config/event_types.yaml). A type with
# no such block (there is none today, but a purely structured-API-only
# type could omit it) is simply never offered to the classifier.
KNOWN_EVENT_TYPES = {t for t in event_registry.known_event_types() if event_registry.spec(t).extraction}

_CORE_FIELD_PY_TYPES: dict[str, type] = {
    "affected_country": str, "affected_sector": str, "severity": int, "estimated_value_eur": float,
}
_PAYLOAD_PY_TYPES: dict[str, type] = {"string": str, "float": float, "int": int}

# currency -> substrings that mark a nearby number as THAT currency.
# Generalizes the original EUR-only marker set so any `currency:` a
# type declares can be checked, and any currency NOT the target one
# counts as a mismatch marker.
_CURRENCY_MARKERS: dict[str, tuple[str, ...]] = {
    "EUR": ("eur", "€"), "USD": ("usd", "$"), "GBP": ("gbp", "£"),
    "CHF": ("chf",), "JPY": ("jpy", "¥"),
}


class _ClassificationFields(BaseModel):
    event_type: str  # one of KNOWN_EVENT_TYPES, or "none"
    quote: str        # the sentence(s) that show this -- a cheap sanity check too
    confidence: float


@tool
def _noop() -> str:
    """Does nothing. Never call this -- it exists only so this stage's
    Agent has at least one real tool registered. A live run found this
    local model reliably invokes ITS structured-output tool only when
    at least one other tool is present to prime tool-calling format at
    all; with `tools=[]` it intermittently emitted no tool call
    whatsoever (an empty response, even after strands' own internal
    forced retry) -- purely a small-local-model quirk with this
    ollama/strands combination, not a sign the text was unclassifiable.
    docs/generalization_plan.md Phase 2's own investigator_agent.py
    never hit this because its agents already have several real tools."""
    return "unused"


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


def _build_fields_model(event_type: str) -> type[BaseModel]:
    """A pydantic model built from event_type's own extraction/payload
    schema -- every registered type gets its own shape, never a single
    schema stretched to fit all of them."""
    spec = event_registry.spec(event_type)
    core_fields = (spec.extraction or {}).get("core_fields", {})

    fields: dict[str, tuple[type, object]] = {
        "event_type": (str, ...),
        "event_date": (str, ...),  # ISO yyyy-mm-dd, validated below -- not trusted as a date type from the model
        "quote": (str, ...),       # the exact sentence(s) the extracted values came from
        "confidence": (float, ...),
    }
    for name, cfg in core_fields.items():
        py_type = _CORE_FIELD_PY_TYPES[name]
        if cfg.get("optional"):
            fields[name] = (Optional[py_type], "" if py_type is str else None)
        else:
            fields[name] = (py_type, ...)
    for name, cfg in (spec.payload or {}).items():
        py_type = _PAYLOAD_PY_TYPES[cfg["type"]]
        if cfg.get("required", True):
            fields[name] = (py_type, ...)
        else:
            fields[name] = (Optional[py_type], cfg.get("default"))

    return create_model(f"ExtractedFields_{event_type}", **fields)


def _describe_field(name: str, cfg: dict) -> str:
    parts = []
    if "pattern" in cfg:
        parts.append(f"must match pattern {cfg['pattern']!r}")
    if "enum" in cfg:
        parts.append(f"must be one of {cfg['enum']}")
    if "min" in cfg or "max" in cfg:
        parts.append(f"must be between {cfg.get('min', '-inf')} and {cfg.get('max', 'inf')}")
    if "min_exclusive" in cfg:
        parts.append(f"must be greater than {cfg['min_exclusive']}")
    if cfg.get("grounded_in_quote"):
        currency = cfg.get("currency")
        parts.append("MUST be a number that literally appears in quote"
                     + (f" (in {currency}, or converted from millions/thousands/percent stated there)"
                        if currency else " (or converted from millions/thousands/percent stated there)"))
    if cfg.get("optional"):
        parts.append("optional -- use an empty/default value if the text doesn't say")
    return f"{name}: " + ("; ".join(parts) if parts else "no format constraint")


def _build_extraction_prompt(event_type: str) -> str:
    spec = event_registry.spec(event_type)
    core_fields = (spec.extraction or {}).get("core_fields", {})
    lines = [
        "You extract ONE structured external event from a short news/notice "
        "text for a commercial bank's internal signal pipeline. Rules:\n",
        f"1. event_type MUST be exactly the string {event_type!r}.\n",
        "2. event_date must be ISO yyyy-mm-dd.\n",
        "3. quote MUST be copied VERBATIM from the source text -- the exact "
        "sentence(s) the other fields came from. Never paraphrase it.\n",
        "4. confidence is your own 0..1 estimate of extraction correctness.\n",
        "5. Never mention financial crime, sanctions, PEP status, or "
        "politically exposed persons -- out of scope, handled elsewhere.\n",
    ]
    n = 6
    for name, cfg in core_fields.items():
        lines.append(f"{n}. {_describe_field(name, cfg)}\n")
        n += 1
    for name, cfg in (spec.payload or {}).items():
        lines.append(f"{n}. {_describe_field(name, cfg)}\n")
        n += 1
    lines.append(
        "If the text does not clearly state a field above, still fill every "
        "field with your best read and set confidence low -- validation "
        "downstream will catch anything wrong."
    )
    return "".join(lines)


def _numeric_grounding_problems(field_name: str, value: float, quote: str, currency: str | None) -> list[str]:
    """Generalizes the original public_tender_award-only EUR-grounding
    check to any field a type marks `grounded_in_quote: true`. Finds
    every number in `quote`, and only counts a match if that number (or
    a thousands/millions/percent-scaled reading of it) is within 1% of
    `value` -- AND, when `currency` is given, the number isn't tagged
    with a DIFFERENT currency marker nearby.

    A live run against a GBP notice once showed why "the target currency
    appears somewhere in the quote" is not enough: the quote was "...no
    EUR figure given" -- a NEGATION, 60+ chars from the actual number,
    which a bare substring check happily matched. So instead: look at a
    short window immediately around EACH number, and only treat it as
    currency-matched if its own nearby marker is the target currency (or
    no currency marker at all) -- never if a different one sits right
    next to it."""
    non_target_markers = (
        [m for cur, markers in _CURRENCY_MARKERS.items() if cur != currency for m in markers]
        if currency else []
    )
    window_chars = 12  # enough for "GBP " or " million EUR", not enough to cross sentences
    quote_numbers: list[tuple[float, bool]] = []
    for m in re.finditer(r"[\d,]+\.?\d*", quote):
        n_str = m.group()
        if not n_str.replace(",", "").replace(".", "").isdigit():
            continue
        n = float(n_str.replace(",", ""))
        window = quote[max(0, m.start() - window_chars):m.end() + window_chars].lower()
        tagged_other_currency = any(marker in window for marker in non_target_markers)
        quote_numbers.append((n, tagged_other_currency))

    av = abs(value)

    def _matches(n: float) -> bool:
        # Scale the QUOTE number n towards value's magnitude, not the
        # other way around: "3.2 million" (n=3.2) must reach 3_200_000
        # (n*1e6); "6%" (n=6) must reach a fractional pct_change of 0.06
        # (n/100). Literal n (no scaling) covers a plain "2,000,000".
        return any(abs(av - candidate) / max(candidate, 1) < 0.01
                  for candidate in (n, n * 1_000, n * 1_000_000, n / 100))

    matching_target = [n for n, tagged_other in quote_numbers if not tagged_other and _matches(n)]
    matching_other = [n for n, tagged_other in quote_numbers if tagged_other and _matches(n)]

    if quote_numbers and not matching_target and matching_other:
        return [f"{field_name} {value} matches a number in the quote tagged with a currency other than "
               f"{currency}, not {currency} (currency mismatch): {quote!r}"]
    if quote_numbers and not matching_target and not matching_other:
        return [f"{field_name} {value} not traceable to any number in quote {quote!r}"]
    return []


def _validate(raw: BaseModel, source_text: str, ingest_date: date, event_type: str) -> list[str]:
    """Every check here runs regardless of what the model claims -- this
    is the gate an extraction cannot talk its way past. Mirrors
    agents/domain_agent.py's _validate but for structured fields instead
    of narrative prose. Driven entirely by event_type's own registry
    schema (external_events/event_registry.py) -- no per-type branch."""
    problems: list[str] = []
    spec = event_registry.spec(event_type)
    core_fields = (spec.extraction or {}).get("core_fields", {})

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

    def _check_field(name: str, value, cfg: dict) -> None:
        if cfg.get("optional") and value in (None, ""):
            return
        pattern = cfg.get("pattern")
        if pattern is not None and isinstance(value, str) and not re.fullmatch(pattern, value):
            problems.append(f"{name} {value!r} does not match required pattern {pattern!r}")
        enum = cfg.get("enum")
        if enum is not None and value not in enum:
            problems.append(f"{name} {value!r} not in {enum}")
        if "min" in cfg and value < cfg["min"]:
            problems.append(f"{name} {value} below minimum {cfg['min']}")
        if "max" in cfg and value > cfg["max"]:
            problems.append(f"{name} {value} above maximum {cfg['max']}")
        if "min_exclusive" in cfg and value <= cfg["min_exclusive"]:
            problems.append(f"{name} {value} must be greater than {cfg['min_exclusive']}")
        if cfg.get("grounded_in_quote"):
            problems.extend(_numeric_grounding_problems(name, value, raw.quote, cfg.get("currency")))

    for name, cfg in core_fields.items():
        _check_field(name, getattr(raw, name), cfg)
    for name, cfg in (spec.payload or {}).items():
        if hasattr(raw, name):
            value = getattr(raw, name)
            if value is not None:
                _check_field(name, value, cfg)

    # Grounding: the quote must actually appear in the source text --
    # otherwise the "citation" is itself hallucinated, which is worse
    # than no citation.
    if raw.quote.strip() and raw.quote.strip() not in source_text:
        problems.append("quote is not a substring of the source text (ungrounded citation)")

    text_blob = f"{raw.quote} {event_type}".lower()
    for banned in BANNED_TERMS:
        if banned in text_blob:
            problems.append(f"extraction touched a disallowed term: {banned!r}")

    return problems


def _classify_event_type(text: str, model, *, attempts: int = 2) -> tuple[str | None, float]:
    """Stage 1: which registered, extraction-eligible type (if any) does
    this text describe? Kept deliberately small and cheap -- a text that
    matches nothing known exits here without a second, more expensive
    structured-extraction call.

Also retries once on `StructuredOutputException` as a second line of
    defense -- see `_noop`'s docstring for the primary fix (a registered
    placeholder tool). Only after `attempts` failures does this
    propagate, for extract_event() to convert into a normal needs_review
    result."""
    from strands import Agent
    from strands.types.exceptions import StructuredOutputException

    known = sorted(KNOWN_EVENT_TYPES)
    classify_template, _prompt_version = load_prompt("event_extraction_classify")
    classify_prompt = classify_template.format(known=known)
    last_error: Exception | None = None
    for _ in range(attempts):
        agent = Agent(model=model, tools=[_noop], system_prompt=classify_prompt)
        try:
            result = agent(f"Classify this text:\n\n{text}", structured_output_model=_ClassificationFields)
        except StructuredOutputException as e:
            last_error = e
            continue
        parsed = result.structured_output
        if parsed is None or parsed.event_type not in known:
            return None, 0.0
        return parsed.event_type, parsed.confidence
    raise last_error


def extract_event(text: str, *, source_name: str, source_ref: str, model,
                   ingest_date: date | None = None) -> ExtractionResult:
    """Local-Ollama structured extraction, validated before it is ever
    treated as a real event. Never raises -- any model/parse failure
    degrades to status='needs_review', exactly like DomainAgent.evaluate()
    degrades to a template rather than an unvalidated claim.

    Two model calls: classify the event type, then extract that type's
    own fields (_build_fields_model). A classification miss (unknown
    type, or the model itself failing) exits after the first call --
    never spends a second call extracting a schema for a type nothing
    downstream can qualify."""
    ingest_date = ingest_date or date.today()
    t0 = time.monotonic()

    try:
        event_type, _classify_confidence = _classify_event_type(text, model)
    except Exception as e:  # noqa: BLE001 -- any agent/model failure -> needs_review, never raises
        return ExtractionResult(source_name, source_ref, None, None, None, "needs_review",
                                [f"classification failed: {type(e).__name__}: {e}"], time.monotonic() - t0)
    if event_type is None:
        return ExtractionResult(source_name, source_ref, None, None, None, "needs_review",
                                ["no known, extractable event_type recognized in this text"],
                                time.monotonic() - t0)

    fields_model = _build_fields_model(event_type)
    try:
        from strands import Agent

        agent = Agent(model=model, tools=[_noop], system_prompt=_build_extraction_prompt(event_type))
        result = agent(f"Extract the event from this text:\n\n{text}", structured_output_model=fields_model)
        raw = result.structured_output
        if raw is None:
            return ExtractionResult(source_name, source_ref, None, None, None, "needs_review",
                                    ["agent returned no structured output"], time.monotonic() - t0)
    except Exception as e:  # noqa: BLE001 -- any agent/model failure -> needs_review, never raises
        return ExtractionResult(source_name, source_ref, None, None, None, "needs_review",
                                [f"{type(e).__name__}: {e}"], time.monotonic() - t0)

    problems = _validate(raw, text, ingest_date, event_type)
    latency = time.monotonic() - t0
    if problems:
        return ExtractionResult(source_name, source_ref, None, raw.quote, raw.confidence,
                                "needs_review", problems, latency)

    spec = event_registry.spec(event_type)
    event = ExogenousEvent(
        event_id=str(uuid.uuid4()), event_date=date.fromisoformat(raw.event_date), event_type=event_type,
        affected_country=getattr(raw, "affected_country", "") or "",
        affected_sector=getattr(raw, "affected_sector", "") or "",
        severity=getattr(raw, "severity", 0) or 0,
        estimated_value_eur=getattr(raw, "estimated_value_eur", 0.0) or 0.0,
        payload={name: getattr(raw, name) for name in (spec.payload or {}) if getattr(raw, name) is not None},
    )
    return ExtractionResult(source_name, source_ref, event, raw.quote, raw.confidence,
                            "extracted", [], latency)
