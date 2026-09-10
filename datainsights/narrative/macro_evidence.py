"""
Evidence packet for an external macro event detection -- the exogenous
counterpart to datainsights/narrative/evidence.py. Deliberately a separate
dataclass, not a reuse of EvidencePacket: the facts available are different
in kind (an event's sector/country match, not a client's own transaction
history), and forcing one shape onto both would let a narrative imply
evidence it doesn't have (e.g. a "baseline_median" that doesn't exist here).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MacroEvidencePacket:
    detection_id: str
    rule_version: str
    client_id: str
    event_id: str
    event_date: str
    event_type: str
    source_name: str
    real_source_type: str
    source_context: str
    affected_country: str
    affected_sector: str
    direction: str
    severity: int
    headline: str
    description: str
    rank: int
    score: float
    allowed_actions: tuple

    def evidence_hash(self) -> str:
        import hashlib
        raw = f"{self.detection_id}|{self.rule_version}|{self.event_id}|{self.severity}|{self.direction}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


ALLOWED_ACTIONS_MACRO = (
    "RM to review whether this client is materially exposed to the event",
    "RM to make routine contact to discuss potential impact and options",
    "No action -- monitor only",
)
