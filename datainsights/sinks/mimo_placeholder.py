"""
MIMO insight record placeholder -- schema-only, NOT WIRED to the real
MIMO platform. docs/decision_record.md Tab 4 ("Primary target -- MIMO
insight record" + "ODS packet config" + "Attribute Keys To Register")
gives the exact shape a real MIMO insight must have; this module builds
that exact shape from a datainsights.correlation.hypothesis.Recommendation
and writes it as JSON, so the mapping code exists and is tested ahead of
Phase 0 (the actual stakeholder conversation with MIMO AIEngine that
would authorize really publishing to it).

Two hard constraints from Tab 4, enforced here:
  - user_story_id max 30 chars (asserted in InsightTester per the record)
  - every insight_attributes key must be one already registered in this
    module's ATTRIBUTE_KEYS list (mirrors "every attribute key must exist
    in the Attributes Catalogue before emission")

This is explicitly the Stage 2 artifact ("prove one NBA locally... shaped
as a MIMO insight record. Still all local." -- Tab 7 AgentCore staging).
Nothing here opens a network connection, writes to Snowflake, or calls
MIMO's Airflow/EMR platform.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

from datainsights.correlation.hypothesis import Recommendation
from datainsights.sinks.base import MAX_CRM_TEXT_CHARS, MAX_USER_STORY_ID_CHARS
from datainsights.sinks.key_mapping import to_prophet_party_id

# docs/decision_record.md Tab 4's attribute keys table -- the ones marked
# NEW (this project's new surface area) plus the ones marked "align"
# (must match the existing live platform's naming). PEP-adjacent /
# FinCrime keys are deliberately absent -- this insight record can never
# carry them (docs/decision_record.md D5, boundary already enforced in
# datainsights/sources/fdm_local.py and agents/domain_agent.py).
ATTRIBUTE_KEYS = {
    "nba_category", "crm_text", "hypothesis", "recommended_action",
    "business_value_score", "confirming_domains", "signal_strength",
    "endogenous_signal_type", "exogenous_event_type", "exogenous_event_source",
    "exogenous_event_date", "evidence_ref", "sizing_basis", "response_actions",
    # NEW, not in the decision record's original table -- added this pass
    # for feedback capture (D6): a stable id a future Pega->CRM response
    # taxonomy join can key on. Schema only; no ML model reads this yet.
    "recommendation_id",
}


class MimoPlaceholderError(Exception):
    pass


def build_insight_attributes(rec: Recommendation) -> dict:
    attrs = {
        "nba_category": rec.nba_category,
        "crm_text": rec.crm_text,
        "hypothesis": rec.hypothesis,
        "recommended_action": rec.recommended_action,
        "business_value_score": rec.business_value_score,
        "confirming_domains": ",".join(rec.confirming_domains),
        "signal_strength": rec.signal_strength,
        "endogenous_signal_type": rec.endogenous_signal_type,
        "exogenous_event_type": rec.exogenous_event_type or "",
        "exogenous_event_source": rec.exogenous_event_source or "",
        "exogenous_event_date": rec.exogenous_event_date or "",
        "evidence_ref": rec.evidence_ref,
        "sizing_basis": rec.sizing_basis,
        "response_actions": ",".join(rec.response_actions),
        "recommendation_id": rec.recommendation_id,
    }
    unregistered = set(attrs) - ATTRIBUTE_KEYS
    if unregistered:
        raise MimoPlaceholderError(
            f"insight_attributes key(s) not in the Attributes Catalogue: {unregistered}"
        )
    if len(attrs["crm_text"]) > MAX_CRM_TEXT_CHARS:
        raise MimoPlaceholderError(
            f"crm_text exceeds the hard {MAX_CRM_TEXT_CHARS}-char limit "
            f"({len(attrs['crm_text'])} chars)"
        )
    return attrs


def build_mimo_insight_record(rec: Recommendation, user_story_id: str,
                                account_specific_insight: bool = True) -> dict:
    if len(user_story_id) > MAX_USER_STORY_ID_CHARS:
        raise MimoPlaceholderError(
            f"user_story_id {user_story_id!r} exceeds the hard "
            f"{MAX_USER_STORY_ID_CHARS}-char limit ({len(user_story_id)} chars)"
        )
    return {
        # D7: mapped from PRTY_ID at the boundary, through the one isolated
        # mapping module (datainsights/sinks/key_mapping.py) -- NOT RUN as
        # a real mapping, identity passthrough until one exists.
        "prophet_party_id": to_prophet_party_id(rec.prty_id),
        "account_id": None if not account_specific_insight else rec.evidence_ref.split(":")[1]
        if ":" in rec.evidence_ref else None,
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
        "insight_attributes": build_insight_attributes(rec),
        "insight_id": f"{user_story_id}:{rec.prty_id}:{rec.nba_category}",
        "record_creation_timestamp": datetime.now(timezone.utc).isoformat(),
        "user_story_id": user_story_id,
    }


def build_ods_packet_config(account_specific_insight: bool = True) -> dict:
    """docs/decision_record.md Tab 4's ODS packet config -- COMMERCIAL
    franchise, matching the decision record's own worked pairing."""
    return {
        "consumer_level_1": "PEGA",
        "consumer_level_2": "COMMERCIAL",
        "consumer_level_3": "CRM",
        "pega_franchise": "COMMERCIAL",
        "account_specific_insight": account_specific_insight,
        "insight_attributes": sorted(ATTRIBUTE_KEYS),
        "requires_budgets": False,
        "cicd_version": "not-run-this-pass",
    }


def write_insights(recommendations: list[Recommendation], out_path: str, as_of: date,
                    user_story_id: str = "datainsights-fdm-poc") -> str:
    """Writes a JSON file of MIMO-shaped insight records -- for local
    inspection only. Never sent anywhere. Returns the path written."""
    records = [build_mimo_insight_record(r, user_story_id) for r in recommendations]
    payload = {
        "generated_as_of": as_of.isoformat(),
        "ods_packet_config": build_ods_packet_config(),
        "insights": records,
        "note": "SCHEMA-ONLY PLACEHOLDER. Never sent to MIMO/Pega. See "
                "docs/decision_record.md Tab 4 and agents/README.md.",
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    return out_path
