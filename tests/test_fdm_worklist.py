"""
Tests for datainsights/fdm_worklist.py -- the RM-facing output.

The load-bearing tests here are the honesty ones: RISK_REVIEW and
ADVISORY_ONLY must never carry a revenue figure (sizing a revenue number
off a risk/compliance signal is the wrong behaviour, per
docs/PROJECT_CONTEXT.md section 6), and every monetary figure that IS
produced must be reachable back to a disclosed assumption.
"""

import os
from datetime import date

import pandas as pd
import pytest
import yaml

from datainsights.correlation.hypothesis import Recommendation
from datainsights.fdm_worklist import (
    RM_WORKLIST_COLUMNS,
    RevenueModel,
    build_rm_worklist,
    indicative_revenue_eur,
    write_rm_digest,
    write_rm_worklist_csv,
)
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")


@pytest.fixture
def rules():
    with open(RULES_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture
def model(rules):
    return RevenueModel.from_rules_dict(rules)


def make_rec(**overrides):
    defaults = dict(
        prty_id="PRTY00036", nba_category="FINANCING_NEED", crm_text="x",
        hypothesis="Test hypothesis.", recommended_action="Offer financing.",
        business_value_score=650, confirming_domains=("deposits", "exogenous"),
        signal_strength=4, endogenous_signal_type="revenue_pattern_change",
        exogenous_event_type="public_tender_award", exogenous_event_source="TED",
        exogenous_event_date="06/07/2025", evidence_ref="EVENT_FINANCIAL:A1:eff=2025-08-21",
        sizing_basis="25pct_of_event_value_illustrative",
        raw_event_value_eur=3_200_000.0, sized_offer_eur=800_000.0,
    )
    defaults.update(overrides)
    return Recommendation(**defaults)


# --- revenue model honesty --------------------------------------------

@pytest.mark.parametrize("category", ["RISK_REVIEW", "ADVISORY_ONLY"])
def test_non_revenue_categories_never_get_a_revenue_figure(category, model):
    assert indicative_revenue_eur(category, 800_000.0, model) is None


def test_unsized_offer_gets_no_revenue_figure(model):
    """If the offer itself could not be honestly sized, a revenue number
    derived from it would be fabricated precision."""
    assert indicative_revenue_eur("FINANCING_NEED", None, model) is None
    assert indicative_revenue_eur("FINANCING_NEED", 0, model) is None


def test_financing_revenue_combines_margin_and_fee(model):
    revenue = indicative_revenue_eur("FINANCING_NEED", 800_000.0, model)
    expected_margin = 800_000.0 * model.financing_nim_pct * (model.financing_assumed_term_months / 12)
    expected_fee = 800_000.0 * model.financing_arrangement_fee_pct
    assert revenue == pytest.approx(expected_margin + expected_fee, rel=1e-6)


def test_treasury_and_hedging_use_their_own_rates(model):
    assert indicative_revenue_eur("TREASURY_OPPORTUNITY", 1_000_000.0, model) == pytest.approx(
        1_000_000.0 * model.treasury_spread_pct, rel=1e-6)
    assert indicative_revenue_eur("HEDGING_NEED", 1_000_000.0, model) == pytest.approx(
        1_000_000.0 * model.hedging_fee_pct, rel=1e-6)


# --- worklist construction ---------------------------------------------

@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_worklist_has_expected_columns_and_client_context(rules):
    source = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    df = build_rm_worklist([make_rec()], source, rules, date(2025, 10, 4))
    assert list(df.columns) == RM_WORKLIST_COLUMNS
    assert len(df) == 1
    row = df.iloc[0]
    assert row["prty_id"] == "PRTY00036"
    assert row["segment"], "RM needs to know the client segment they're calling"
    assert row["revenue_mechanism"], "RM needs to know how this earns revenue"
    assert row["talking_point"], "RM needs something to actually say"
    assert row["why_now"], "RM needs to know why this is time-sensitive"


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_revenue_categories_rank_above_non_revenue_ones(rules):
    source = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    recs = [
        make_rec(prty_id="PRTY00001", nba_category="RISK_REVIEW", signal_strength=5,
                  sizing_basis="not_sized_this_pass", raw_event_value_eur=None, sized_offer_eur=None),
        make_rec(prty_id="PRTY00036", nba_category="FINANCING_NEED", signal_strength=2),
    ]
    df = build_rm_worklist(recs, source, rules, date(2025, 10, 4))
    # the revenue-earning row ranks first even though its signal is weaker
    assert df.iloc[0]["nba_category"] == "FINANCING_NEED"
    assert df.iloc[-1]["nba_category"] == "RISK_REVIEW"
    assert pd.isna(df.iloc[-1]["indicative_revenue_eur"])


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_empty_recommendations_returns_empty_shaped_frame(rules):
    source = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    df = build_rm_worklist([], source, rules, date(2025, 10, 4))
    assert df.empty
    assert list(df.columns) == RM_WORKLIST_COLUMNS


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_csv_and_digest_write_and_disclose_illustrative(rules, tmp_path):
    source = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    df = build_rm_worklist([make_rec()], source, rules, date(2025, 10, 4))

    csv_path = write_rm_worklist_csv(df, os.path.join(tmp_path, "wl", "worklist.csv"))
    assert os.path.exists(csv_path)
    assert len(pd.read_csv(csv_path)) == 1

    digest_path = write_rm_digest(df, os.path.join(tmp_path, "wl", "digest.md"), date(2025, 10, 4))
    text = open(digest_path).read()
    assert "illustrative" in text.lower(), "every monetary figure must be labelled illustrative"
    assert "Synthetic proof-of-concept" in text
    assert "not this bank's pricing" in text.lower()
    assert "PRTY00036" in text
