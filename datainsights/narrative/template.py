"""
Deterministic template narrative -- the baseline required before comparing
an LLM narrator against it (project instructions section 8). No model call,
no invented facts: every value comes straight from the evidence packet.
"""

from __future__ import annotations

from datainsights.narrative.evidence import EvidencePacket


def render(evidence: EvidencePacket) -> dict:
    mad_txt = f"{evidence.mad_multiples:.1f}x" if evidence.mad_multiples == evidence.mad_multiples else "n/a"
    observed_facts = (
        f"On {evidence.event_date}, account {evidence.account_id} (client {evidence.client_id}) "
        f"received a credit of {evidence.flagged_amount:,.2f} {evidence.currency}. "
        f"This account's trailing {evidence.baseline_n}-transaction baseline median inflow was "
        f"{evidence.baseline_median:,.2f} {evidence.currency} -- the flagged amount is "
        f"approximately {mad_txt} the typical deviation above that baseline."
    )
    interpretation = (
        "This is a statistical size anomaly relative to this client's own recent payment "
        "history. It does not by itself establish the payment's source, purpose, or business "
        "significance."
    )
    suggested_action = evidence.allowed_actions[0]
    caveats = (
        "Synthetic POC output for RM review, not a business conclusion. Detection uses "
        "point-in-time data only (event-time replay, not availability-aware backtesting). "
        "Rule thresholds are provisional and unvalidated against business outcomes."
    )
    return {
        "detection_id": evidence.detection_id,
        "observed_facts": observed_facts,
        "interpretation": interpretation,
        "suggested_action": suggested_action,
        "evidence_references": [evidence.detection_id],
        "caveats": caveats,
        "narrative_source": "deterministic_template",
    }
