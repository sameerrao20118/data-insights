"""
Sink contract test (M7): the SAME Recommendation, fed through the RM
worklist, the MIMO JSON sink, and the Pega event mock sink, must yield
consistent category, hypothesis, recommended_action, and
recommendation_id across all of them -- no sink computes its own. See
docs/decision_record.md Tab 4 and agents/README.md's D1 boundary (signal
enrichment feeding Pega CDH, not a second decisioning path each sink
could quietly reinterpret).
"""

import os
from datetime import date

import pytest
import yaml

from datainsights.correlation.hypothesis import Recommendation
from datainsights.fdm_worklist import build_rm_worklist
from datainsights.sinks.mimo_placeholder import build_mimo_insight_record
from datainsights.sinks.pega_event_mock import build_pega_event
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")


@pytest.fixture
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


def make_rec(**overrides):
    defaults = dict(
        prty_id="PRTY00036", nba_category="FINANCING_NEED",
        crm_text="Revenue Pattern Change observed. Offer working-capital financing.",
        hypothesis="A structural step-change in incoming payment pattern often reflects "
                   "genuine business growth.",
        recommended_action="Offer working-capital financing of ~EUR 800,000 (illustrative).",
        business_value_score=650, confirming_domains=("deposits", "exogenous"),
        signal_strength=4, endogenous_signal_type="revenue_pattern_change",
        exogenous_event_type="public_tender_award", exogenous_event_source="TED",
        exogenous_event_date="06/06/2025", evidence_ref="EVENT_FINANCIAL:AGR000123:eff=2025-06-06",
        sizing_basis="25pct_of_event_value_illustrative",
        raw_event_value_eur=3_200_000.0, sized_offer_eur=800_000.0,
        recommendation_id="deadbeefcafe1234567",
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


def test_mimo_and_pega_sinks_agree_on_category_hypothesis_action_and_id():
    rec = make_rec()
    mimo = build_mimo_insight_record(rec, user_story_id="test-story")
    pega = build_pega_event(rec)

    assert mimo["insight_attributes"]["nba_category"] == pega["event_type"] == rec.nba_category
    assert mimo["insight_attributes"]["hypothesis"] == pega["hypothesis"] == rec.hypothesis
    assert (mimo["insight_attributes"]["recommended_action"]
            == pega["recommended_action"] == rec.recommended_action)
    assert mimo["insight_attributes"]["recommendation_id"] == pega["recommendation_id"] == rec.recommendation_id
    assert mimo["insight_attributes"]["confirming_domains"] == ",".join(pega["confirming_domains"])
    assert mimo["insight_attributes"]["response_actions"] == ",".join(pega["response_actions"])


def test_same_recommendation_id_is_stable_not_regenerated_per_sink():
    """A future Pega->CRM feedback response has to join back to exactly
    this recommendation -- if two sinks derived their own id, or the id
    changed per render, that join would silently break."""
    rec = make_rec()
    pega_first = build_pega_event(rec)
    pega_second = build_pega_event(rec)
    assert pega_first["recommendation_id"] == pega_second["recommendation_id"] == rec.recommendation_id


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_rm_worklist_row_agrees_with_the_other_sinks_too(rules):
    """The worklist -- the one sink with genuinely different inputs
    (client segment/sector/country from `source`) -- still renders the
    SAME category/hypothesis/action, not its own."""
    rec = make_rec()
    source = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    df = build_rm_worklist([rec], source, rules, date(2025, 10, 4))
    row = df.iloc[0]

    mimo = build_mimo_insight_record(rec, user_story_id="test-story")
    pega = build_pega_event(rec)

    assert row["nba_category"] == mimo["insight_attributes"]["nba_category"] == pega["event_type"]
    assert row["hypothesis"] == mimo["insight_attributes"]["hypothesis"] == pega["hypothesis"]
    assert (row["recommended_action"] == mimo["insight_attributes"]["recommended_action"]
            == pega["recommended_action"])
