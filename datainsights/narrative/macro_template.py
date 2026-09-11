"""
Deterministic template for a macro-event narrative -- same role as
template.py: the always-available baseline, and the fallback if the LLM
narrator's output fails validation. Same output dict shape as
template.render() so digest.py and state.py need no changes to handle
either event category.
"""

from __future__ import annotations

import math

from datainsights.narrative.macro_evidence import MacroEvidencePacket


def render(evidence: MacroEvidencePacket) -> dict:
    has_value = evidence.estimated_value_eur is not None and not (
        isinstance(evidence.estimated_value_eur, float) and math.isnan(evidence.estimated_value_eur)
    )
    value_clause = (
        f" This event carries a simulated estimated value of €{evidence.estimated_value_eur:,.0f}."
        if has_value else ""
    )
    observed_facts = (
        f"On {evidence.event_date}, an external event was recorded: \"{evidence.headline}\" "
        f"(source: {evidence.source_name}, severity {evidence.severity}/5, "
        f"{evidence.direction} direction).{value_clause} Client {evidence.client_id} matches "
        f"this event's scope by sector/country."
    )
    interpretation = (
        "This client's sector and/or country overlaps the event's scope. This does not "
        "establish that this specific client is actually affected, only that they fall "
        "within the event's plausible reach -- a broader, weaker claim than a client-specific "
        "signal would be."
    )
    suggested_action = evidence.allowed_actions[0]
    caveats = (
        "Synthetic POC output for RM review, not a business conclusion. This event is "
        "simulated, not a real occurrence -- see external_events/README.md for the real "
        f"source ({evidence.real_source_type}) this event type would come from in "
        f"production, and what that source would actually bring: {evidence.source_context} "
        "Sector/country overlap is not confirmation of client-specific impact."
    )
    return {
        "detection_id": evidence.detection_id,
        "observed_facts": observed_facts,
        "interpretation": interpretation,
        "suggested_action": suggested_action,
        "evidence_references": [evidence.detection_id, evidence.event_id],
        "caveats": caveats,
        "narrative_source": "deterministic_template",
    }
