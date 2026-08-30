"""
Minimal structured evidence packet handed to a narrator (template or LLM).
Contains ONLY facts already established by the detector/ranker -- no field
here is something a narrator is free to invent or embellish.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidencePacket:
    detection_id: str
    rule_version: str
    client_id: str
    account_id: str
    currency: str
    event_date: str
    flagged_amount: float
    baseline_median: float
    baseline_n: int
    mad_multiples: float
    rank: int
    score: float
    allowed_actions: tuple  # the ONLY follow-up actions a narrative may suggest

    def evidence_hash(self) -> str:
        import hashlib
        raw = f"{self.detection_id}|{self.rule_version}|{self.flagged_amount}|{self.baseline_median}|{self.baseline_n}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


ALLOWED_ACTIONS = (
    "RM to review the transaction and recent account activity",
    "RM to make routine contact with the client to understand the payment's origin",
    "No action -- monitor only",
)
