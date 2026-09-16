"""
RM Copilot (docs/agentic_plan.md A3) -- answers an RM's question about
ONE recommendation, using ONLY the fields already computed for that row
in the RM worklist (datainsights/fdm_worklist.py's RM_WORKLIST_COLUMNS).

Deliberately the narrowest possible scope: no tool, no DataSource
access, no ability to query another client -- the "context" is one
dict (one worklist row), so there is structurally nothing else for the
model to leak even if it tried. This is stricter than
agents/investigator_agent.py's read-only-but-live-query tools, on
purpose: a copilot an RM chats with needs the tightest boundary of
anything in this repo, since it's the one agent a person interacts with
directly rather than just reading validated output from.

Same validate-or-fallback discipline as every other agent here: every
number the answer states must trace to a field in the row, evidence_ref
citations must be the row's own (there is only one to cite), banned
terms are rejected. Any failure falls back to a canned answer built
directly from the row's fields, never an unvalidated claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from agents.domain_agent import BANNED_TERMS

# Fields an RM might ask about -- everything else in the row (rank,
# segment, evidence_ref, sizing_basis, ...) is fair game as context but
# these are the ones worth surfacing by name if the model needs prompting.
CONTEXT_FIELDS = (
    "prty_id", "segment", "sector", "country", "nba_category", "revenue_mechanism",
    "indicative_revenue_eur", "indicative_offer_eur", "why_now", "hypothesis",
    "recommended_action", "talking_point", "confirming_domains", "signal_strength",
    "endogenous_signal_type", "exogenous_event_type", "exogenous_event_date",
    "evidence_ref", "sizing_basis", "response_actions",
)


class CopilotFields(BaseModel):
    answer: str


@dataclass
class CopilotAnswer:
    answer: str
    narrative_source: str
    status: str = "answered"  # "answered" | "fallback"


def _fallback_answer(row: dict, reason: str) -> CopilotAnswer:
    lines = [f"{k.replace('_', ' ')}: {row[k]}" for k in CONTEXT_FIELDS if k in row and row[k] not in (None, "")]
    return CopilotAnswer(
        answer="I can only answer from this recommendation's own evidence -- here's everything on file:\n"
               + "\n".join(f"- {line}" for line in lines),
        narrative_source=f"deterministic_template (copilot fallback: {reason})", status="fallback",
    )


def _numbers_in(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"[\d,]+\.?\d*", text)
            if n.replace(",", "").replace(".", "").isdigit()]


def _validate(answer: str, row: dict) -> list[str]:
    problems = []
    if not answer.strip():
        problems.append("empty answer")
    lowered = answer.lower()
    for banned in BANNED_TERMS:
        if banned in lowered:
            problems.append(f"answer used a disallowed term: {banned!r}")
    # Every number stated must trace to a numeric field on the row (or be
    # small enough to be a rank/count/percentage restatement) -- same
    # discipline as domain_agent.py's _numeric_tolerance_ok, scoped to
    # one row instead of a tool_evidence dict.
    reference_numbers = []
    for k, v in row.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v:  # v==v excludes NaN
            reference_numbers.append(float(v))
        elif isinstance(v, str):
            # String fields (sizing_basis, hypothesis, recommended_action, ...)
            # carry real facts too -- e.g. sizing_basis literally contains
            # "25pct_of_event_value_illustrative". A percentage or figure
            # restated from one of THOSE is grounded, not invented.
            reference_numbers.extend(_numbers_in(v))
    found = _numbers_in(answer)
    unmatched = [f for f in found if f > 10  # ignore small numbers (e.g. "5/5", "3 domains") -- too easy to coincide
                and not any(abs(f - r) <= max(0.01, abs(r) * 0.01) for r in reference_numbers)]
    if unmatched:
        problems.append(f"answer states number(s) not traceable to this row: {unmatched}")
    return problems


def ask(question: str, row: dict, model) -> CopilotAnswer:
    """`row` is one dict from the RM worklist CSV (or equivalent) -- the
    ONLY data this function's model call can see. Never raises."""
    context_lines = "\n".join(f"- {k}: {row[k]}" for k in CONTEXT_FIELDS if k in row and row[k] not in (None, ""))
    system_prompt = (
        "You are answering a relationship manager's question about ONE client "
        "recommendation, using ONLY the facts below. Rules:\n"
        "1. Use ONLY these facts -- never invent a number, a client detail, or "
        "any fact not listed here.\n"
        "2. If the question asks about something not in these facts, say so "
        "plainly -- do not guess or extrapolate.\n"
        "3. Every number in your answer must come from these facts.\n"
        "4. Never mention financial crime, sanctions, PEP status, or politically "
        "exposed persons.\n"
        "5. Keep the answer to 2-3 sentences.\n"
        f"Facts about this recommendation:\n{context_lines}"
    )
    try:
        from strands import Agent

        agent = Agent(model=model, tools=[], system_prompt=system_prompt)
        result = agent(question, structured_output_model=CopilotFields)
        parsed = result.structured_output
        if parsed is None:
            return _fallback_answer(row, "agent returned no structured output")
    except Exception as e:  # noqa: BLE001 -- any failure -> fallback, never raises
        return _fallback_answer(row, f"{type(e).__name__}: {e}")

    problems = _validate(parsed.answer, row)
    if problems:
        return _fallback_answer(row, "; ".join(problems))

    return CopilotAnswer(answer=parsed.answer, narrative_source="strands+ollama:copilot", status="answered")
