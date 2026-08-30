"""
Offline, sampled LLM judge -- semantic checks a deterministic validator
can't do (faithfulness, action relevance, uncertainty handling, clarity).
Deterministic checks (schema validity, numeric consistency, evidence-
reference validity, allowed-action membership) already happened in
ollama_narrator._validate before a narrative is ever accepted; this judge
only runs against a SAMPLE of accepted narratives, offline, not on every
monitoring event (project instructions section 8).

Uses a DIFFERENT model than the narrator (see config/rules.yaml comment)
to reduce correlated narrator/judge errors -- not full independence (both
are still local Ollama models with unknown shared training data/failure
modes), and that limitation is surfaced in the report, not hidden.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from datainsights.narrative.evidence import EvidencePacket

RUBRIC = """You are grading a short RM-facing note written about ONE flagged
banking transaction. You are given the evidence packet (ground truth facts)
and the narrative that was generated from it. Score each dimension 1-5
(5=best), citing which evidence field supports your score:

- faithfulness: every claim in the narrative is directly supported by the
  evidence packet; no invented facts, no unsupported business
  interpretation (e.g. claiming it's a "tender payment" when evidence only
  shows a large credit amount is a faithfulness violation -> score 1-2).
- action_relevance: suggested_action is reasonable given the evidence.
- uncertainty_handling: the narrative is appropriately cautious given this
  is a single statistical anomaly, not confirmed business fact.
- clarity: an RM could read this in 10 seconds and understand what to do.

Respond with JSON only."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "faithfulness": {"type": "integer", "minimum": 1, "maximum": 5},
        "faithfulness_rationale": {"type": "string"},
        "action_relevance": {"type": "integer", "minimum": 1, "maximum": 5},
        "uncertainty_handling": {"type": "integer", "minimum": 1, "maximum": 5},
        "clarity": {"type": "integer", "minimum": 1, "maximum": 5},
        "flagged_for_human_review": {"type": "boolean"},
    },
    "required": ["faithfulness", "action_relevance", "uncertainty_handling",
                 "clarity", "flagged_for_human_review"],
}


def judge_one(evidence: EvidencePacket, narrative: dict, base_url: str, model: str,
               timeout_seconds: int) -> dict:
    packet = {
        "event_date": evidence.event_date,
        "flagged_amount": evidence.flagged_amount,
        "currency": evidence.currency,
        "baseline_median": evidence.baseline_median,
        "baseline_n_prior_transactions": evidence.baseline_n,
    }
    user_content = (
        "Evidence packet:\n" + json.dumps(packet, indent=2) +
        "\n\nNarrative under review:\n" + json.dumps({
            "observed_facts": narrative.get("observed_facts"),
            "interpretation": narrative.get("interpretation"),
            "suggested_action": narrative.get("suggested_action"),
        }, indent=2)
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "format": JUDGE_SCHEMA,
        "options": {"temperature": 0},
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        scores = json.loads(raw["message"]["content"])
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, OSError) as e:
        return {
            "detection_id": evidence.detection_id,
            "judge_status": "failed",
            "judge_error": f"{type(e).__name__}: {e}",
        }
    scores["detection_id"] = evidence.detection_id
    scores["judge_status"] = "ok"
    scores["judge_model"] = model
    scores["narrative_source"] = narrative.get("narrative_source")
    return scores
