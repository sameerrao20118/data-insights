"""
Pega event sink -- schema-only mock for SFPGMOD004_DS_PEGA_ORGANISATION_EVENT
(docs/decision_record.md Tab 4, "Secondary target -- Pega presentation
layer": "An NBA recommendation IS an event about an organisation.
SFPGMOD004 is the natural insertion point if the MIMO route is not
taken."). Never wired to a real Pega/S3 publish -- NOT RUN, mock only,
same discipline as mimo_placeholder.py's own header.

Exists so the sink contract (same Recommendation -> same category,
hypothesis, action, and recommendation_id) is provable across every
output route this pass builds toward, not just the primary MIMO one --
D2 says build detectors as MIMO insights first, but D1's positioning
(signal enrichment feeding Pega CDH) doesn't rule out this secondary
route, and Tab 4 documents it as a real fallback insertion point.

Column names below are this repo's best-effort mock of "an event about
an organisation" (Tab 4's own framing) -- no captured DDL for SFPGMOD004
exists in docs/decision_record.md beyond the table name and its purpose.
Treat every key here as illustrative until a real Pega HLDD extract is
available; do not present this as a verified real schema.
"""

from __future__ import annotations

from datetime import datetime, timezone

from datainsights.correlation.hypothesis import Recommendation
from datainsights.sinks.base import MAX_CRM_TEXT_CHARS
from datainsights.sinks.key_mapping import to_enterprise_customer_id


class PegaEventMockError(Exception):
    pass


def build_pega_event(rec: Recommendation, event_source_system: str = "datainsights-fdm-poc") -> dict:
    """One SFPGMOD004-shaped mock row for `rec` -- category, hypothesis,
    recommended_action and recommendation_id are read straight off the
    Recommendation, never recomputed (see tests/test_sink_contract.py)."""
    if len(rec.crm_text) > MAX_CRM_TEXT_CHARS:
        raise PegaEventMockError(
            f"crm_text exceeds the hard {MAX_CRM_TEXT_CHARS}-char limit ({len(rec.crm_text)} chars)"
        )
    return {
        # D7: mapped from PRTY_ID at the boundary, through the one isolated
        # mapping module -- NOT RUN as a real mapping, identity passthrough
        # until Pega's FDM key alignment actually lands.
        "organisation_id": to_enterprise_customer_id(rec.prty_id),
        "event_type": rec.nba_category,
        "event_source_system": event_source_system,
        "event_timestamp": datetime.now(timezone.utc).isoformat(),
        "recommendation_id": rec.recommendation_id,
        "crm_text": rec.crm_text,
        "hypothesis": rec.hypothesis,
        "recommended_action": rec.recommended_action,
        "business_value_score": rec.business_value_score,
        "signal_strength": rec.signal_strength,
        "confirming_domains": list(rec.confirming_domains),
        # Live taxonomy, reused verbatim (docs/decision_record.md Tab 1/2)
        # -- this is the field a future Pega->CRM response join writes
        # back into, keyed on recommendation_id. No model reads it here.
        "response_actions": list(rec.response_actions),
    }
