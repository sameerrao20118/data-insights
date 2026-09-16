"""
M6 verification for datainsights/sinks/mimo_placeholder.py.
"""

import json
import os
from datetime import date

import pytest

from datainsights.correlation.hypothesis import Recommendation
from datainsights.sinks.mimo_placeholder import (
    MAX_CRM_TEXT_CHARS,
    MAX_USER_STORY_ID_CHARS,
    MimoPlaceholderError,
    build_insight_attributes,
    build_mimo_insight_record,
    build_ods_packet_config,
    write_insights,
)


def make_rec(**overrides):
    defaults = dict(
        prty_id="PRTY00036", nba_category="FINANCING_NEED", crm_text="Revenue growth observed.",
        hypothesis="Test hypothesis.", recommended_action="Offer financing.",
        business_value_score=650, confirming_domains=("deposits", "exogenous"),
        signal_strength=4, endogenous_signal_type="revenue_pattern_change",
        exogenous_event_type="public_tender_award", exogenous_event_source="TED",
        exogenous_event_date="06/06/2025", evidence_ref="EVENT_FINANCIAL:AGR000123:eff=2025-06-06",
        sizing_basis="25pct_of_event_value_illustrative",
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


def test_build_insight_attributes_only_uses_registered_keys():
    attrs = build_insight_attributes(make_rec())
    from datainsights.sinks.mimo_placeholder import ATTRIBUTE_KEYS
    assert set(attrs.keys()) <= ATTRIBUTE_KEYS


def test_crm_text_over_200_chars_raises():
    with pytest.raises(MimoPlaceholderError, match="200-char"):
        build_insight_attributes(make_rec(crm_text="x" * 201))


def test_user_story_id_over_30_chars_raises():
    with pytest.raises(MimoPlaceholderError, match="30-char"):
        build_mimo_insight_record(make_rec(), user_story_id="x" * 31)


def test_user_story_id_within_limit_ok():
    record = build_mimo_insight_record(make_rec(), user_story_id="datainsights-fdm-poc")
    assert record["user_story_id"] == "datainsights-fdm-poc"
    assert record["prophet_party_id"] == "PRTY00036"


def test_insight_record_never_contains_fincrime_terms():
    """No key or value in the built record may mention FinCrime/PEP
    concepts -- structural check, not just absence-by-omission."""
    rec = make_rec()
    record = build_mimo_insight_record(rec, user_story_id="test")
    blob = json.dumps(record).lower()
    for banned in ("pep", "sanction", "fincrime", "money launder"):
        assert banned not in blob


def test_ods_packet_config_shape():
    config = build_ods_packet_config()
    assert config["consumer_level_2"] == "COMMERCIAL"
    assert config["pega_franchise"] == "COMMERCIAL"
    assert config["requires_budgets"] is False


def test_write_insights_produces_valid_json(tmp_path):
    out_path = os.path.join(tmp_path, "insights.json")
    written = write_insights([make_rec()], out_path, as_of=date(2026, 8, 20))
    assert written == out_path
    with open(out_path) as f:
        payload = json.load(f)
    assert len(payload["insights"]) == 1
    assert "SCHEMA-ONLY PLACEHOLDER" in payload["note"]
    assert payload["insights"][0]["prophet_party_id"] == "PRTY00036"
