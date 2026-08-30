"""
Local Ollama narrator using structured (JSON-schema-constrained) output.

Verified against https://docs.ollama.com/capabilities/structured-outputs
(checked 2026-08-30): POST /api/chat with a JSON schema in the "format"
field; low temperature recommended for deterministic structured output;
Ollama's cloud-routed models do NOT support structured outputs (one more
reason this project never selects a "-cloud" tag -- see
datainsights/config.py's LLMConfig validator).

Falls back to the deterministic template (never raises) if Ollama is
unreachable, times out, or returns output that fails schema/numeric
validation after max_repair_attempts.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from datainsights.narrative import template
from datainsights.narrative.evidence import ALLOWED_ACTIONS, EvidencePacket

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "observed_facts": {"type": "string"},
        "interpretation": {"type": "string"},
        "suggested_action": {"type": "string", "enum": list(ALLOWED_ACTIONS)},
        "caveats": {"type": "string"},
    },
    "required": ["observed_facts", "interpretation", "suggested_action", "caveats"],
}

SYSTEM_PROMPT = (
    "You write a short, cautious note for a bank relationship manager about ONE "
    "flagged transaction. You are given a small evidence packet of already-verified "
    "facts. Rules:\n"
    "1. Every number you state (amount, date, baseline) MUST come from the evidence "
    "packet, verbatim or trivially rounded. Never invent a number.\n"
    "2. Never claim to know the payment's business purpose (e.g. never say 'tender', "
    "'contract award', 'investable surplus') -- this is a statistical size anomaly "
    "only, nothing more is established.\n"
    "3. suggested_action MUST be exactly one of the allowed actions given, verbatim.\n"
    "4. If you are not confident you can follow rules 1-3, write caveats explaining "
    "why instead of guessing.\n"
    "This is a synthetic proof-of-concept dataset. Say so is not required in your "
    "text (it's added automatically) but never claim real-world validity."
)


def _build_prompt(evidence: EvidencePacket) -> str:
    packet = {
        "event_date": evidence.event_date,
        "flagged_amount": evidence.flagged_amount,
        "currency": evidence.currency,
        "baseline_median": evidence.baseline_median,
        "baseline_n_prior_transactions": evidence.baseline_n,
        "mad_multiples_above_baseline": round(evidence.mad_multiples, 2) if evidence.mad_multiples == evidence.mad_multiples else None,
        "allowed_suggested_actions": list(evidence.allowed_actions),
    }
    return (
        "Evidence packet (JSON):\n" + json.dumps(packet, indent=2) +
        "\n\nRespond with JSON matching this schema:\n" + json.dumps(RESPONSE_SCHEMA, indent=2)
    )


def _call_ollama(base_url: str, model: str, evidence: EvidencePacket, timeout_seconds: int) -> dict:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(evidence)},
        ],
        "stream": False,
        "format": RESPONSE_SCHEMA,
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    content = raw["message"]["content"]
    return json.loads(content)


def _validate(parsed: dict, evidence: EvidencePacket) -> list[str]:
    """Deterministic checks: schema validity, numeric consistency, allowed
    action. Returns a list of problems (empty = passed)."""
    problems = []
    for key in ("observed_facts", "interpretation", "suggested_action", "caveats"):
        if key not in parsed or not isinstance(parsed[key], str) or not parsed[key].strip():
            problems.append(f"missing or empty field: {key}")
    if parsed.get("suggested_action") not in evidence.allowed_actions:
        problems.append(f"suggested_action not in allowed set: {parsed.get('suggested_action')!r}")

    text_blob = (parsed.get("observed_facts", "") + " " + parsed.get("interpretation", "")).lower()
    for banned in ("tender", "contract award", "investable surplus", "net fx exposure"):
        if banned in text_blob:
            problems.append(f"narrative used a disallowed interpretive claim: '{banned}'")

    # numeric consistency: some number in observed_facts must be within 1%
    # of flagged_amount -- tolerant to rounding/formatting differences,
    # not a brittle string match
    found_numbers = [
        float(n.replace(",", ""))
        for n in re.findall(r"[\d,]+\.?\d*", parsed.get("observed_facts", ""))
        if n.replace(",", "").replace(".", "").isdigit()
    ]
    tol = max(1.0, abs(evidence.flagged_amount) * 0.01)
    if not any(abs(n - evidence.flagged_amount) <= tol for n in found_numbers):
        problems.append("flagged_amount does not appear to be referenced in observed_facts")
    return problems


def render(evidence: EvidencePacket, base_url: str, model: str, timeout_seconds: int,
           max_repair_attempts: int) -> dict:
    last_problems: list[str] = []
    for attempt in range(max_repair_attempts + 1):
        try:
            parsed = _call_ollama(base_url, model, evidence, timeout_seconds)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, OSError) as e:
            last_problems = [f"call failed: {type(e).__name__}: {e}"]
            break  # a connection/parse failure won't be fixed by retrying identically
        problems = _validate(parsed, evidence)
        if not problems:
            result = {
                "detection_id": evidence.detection_id,
                "observed_facts": parsed["observed_facts"],
                "interpretation": parsed["interpretation"],
                "suggested_action": parsed["suggested_action"],
                "evidence_references": [evidence.detection_id],
                "caveats": parsed["caveats"] + " Synthetic POC output for RM review, not a validated business conclusion.",
                "narrative_source": f"ollama:{model}",
            }
            return result
        last_problems = problems

    # fall back to deterministic template -- never raise, never emit
    # unvalidated LLM output
    fallback = template.render(evidence)
    fallback["narrative_source"] = f"deterministic_template (ollama fallback: {'; '.join(last_problems)})"
    return fallback
