"""
Local Ollama narrator for external macro events -- same structured-output,
validate-or-fallback pattern as ollama_narrator.py, different rules for
what counts as a faithful claim (no numeric amount to check; the thing to
police here is causal overreach -- "this affects client X" is a stronger
claim than the evidence supports).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from datainsights.narrative import macro_template
from datainsights.narrative.macro_evidence import ALLOWED_ACTIONS_MACRO, MacroEvidencePacket

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "observed_facts": {"type": "string"},
        "interpretation": {"type": "string"},
        "suggested_action": {"type": "string", "enum": list(ALLOWED_ACTIONS_MACRO)},
        "caveats": {"type": "string"},
    },
    "required": ["observed_facts", "interpretation", "suggested_action", "caveats"],
}

SYSTEM_PROMPT = (
    "You write a short, cautious note for a bank relationship manager about an external "
    "market/industry/political event that MIGHT affect one client. You are given a small "
    "evidence packet of already-verified facts. Rules:\n"
    "1. The evidence only establishes that this client's sector and/or country overlaps "
    "the event's scope -- NOT that this specific client is actually affected. Never claim "
    "certainty of impact; use hedged language ('may be exposed to', 'could be affected by').\n"
    "2. Never invent a financial amount, a specific mechanism of harm/benefit, or a product "
    "recommendation beyond the three allowed actions given.\n"
    "3. This event is SYNTHETIC/simulated, not a real occurrence -- never write as if it "
    "really happened.\n"
    "4. suggested_action MUST be exactly one of the allowed actions given, verbatim.\n"
    "5. If you are not confident you can follow rules 1-4, write caveats explaining why "
    "instead of guessing."
)


def _build_prompt(evidence: MacroEvidencePacket) -> str:
    packet = {
        "event_date": evidence.event_date,
        "event_type": evidence.event_type,
        "headline": evidence.headline,
        "description": evidence.description,
        "source_name": evidence.source_name,
        "affected_sector": evidence.affected_sector or "(broad/all sectors)",
        "affected_country": evidence.affected_country or "(EU-wide)",
        "direction": evidence.direction,
        "severity_1_to_5": evidence.severity,
        "client_id": evidence.client_id,
        "allowed_suggested_actions": list(evidence.allowed_actions),
    }
    return (
        "Evidence packet (JSON):\n" + json.dumps(packet, indent=2) +
        "\n\nRespond with JSON matching this schema:\n" + json.dumps(RESPONSE_SCHEMA, indent=2)
    )


def _call_ollama(base_url: str, model: str, evidence: MacroEvidencePacket, timeout_seconds: int) -> dict:
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
    return json.loads(raw["message"]["content"])


CERTAINTY_PHRASES = ("will be affected", "is affected", "definitely", "certainly", "confirmed")
HEDGE_PHRASES = ("may", "could", "might", "possibly", "potentially", "plausibly")


def _validate(parsed: dict, evidence: MacroEvidencePacket) -> list[str]:
    problems = []
    for key in ("observed_facts", "interpretation", "suggested_action", "caveats"):
        if key not in parsed or not isinstance(parsed[key], str) or not parsed[key].strip():
            problems.append(f"missing or empty field: {key}")
    if parsed.get("suggested_action") not in evidence.allowed_actions:
        problems.append(f"suggested_action not in allowed set: {parsed.get('suggested_action')!r}")

    text_blob = (parsed.get("observed_facts", "") + " " + parsed.get("interpretation", "")).lower()
    if any(p in text_blob for p in CERTAINTY_PHRASES):
        problems.append("narrative asserts certainty of client-specific impact, "
                         "which the sector/country match evidence does not support")
    if not any(p in text_blob for p in HEDGE_PHRASES):
        problems.append("narrative lacks hedged language for an unconfirmed sector/country match")
    return problems


def render(evidence: MacroEvidencePacket, base_url: str, model: str, timeout_seconds: int,
           max_repair_attempts: int) -> dict:
    last_problems: list[str] = []
    for _ in range(max_repair_attempts + 1):
        try:
            parsed = _call_ollama(base_url, model, evidence, timeout_seconds)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, OSError) as e:
            last_problems = [f"call failed: {type(e).__name__}: {e}"]
            break
        problems = _validate(parsed, evidence)
        if not problems:
            return {
                "detection_id": evidence.detection_id,
                "observed_facts": parsed["observed_facts"],
                "interpretation": parsed["interpretation"],
                "suggested_action": parsed["suggested_action"],
                "evidence_references": [evidence.detection_id, evidence.event_id],
                "caveats": parsed["caveats"] + " Synthetic POC output for RM review, not a "
                    "validated business conclusion. Real source in production: "
                    f"{evidence.real_source_type} -- {evidence.source_context}",
                "narrative_source": f"ollama:{model}",
            }
        last_problems = problems

    fallback = macro_template.render(evidence)
    fallback["narrative_source"] = f"deterministic_template (ollama fallback: {'; '.join(last_problems)})"
    return fallback
