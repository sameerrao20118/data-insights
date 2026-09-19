"""
R2 acceptance (docs/refactor_plan.md §4, Wave 1): a category is a YAML
entry, not a string literal agreed on by ~9 Python files. These tests
register categories in fixture YAML ONLY and prove they flow through
assemble(), the worklist's revenue model, and the dashboard's category
card with zero code edits -- the same pattern tests/test_domain_registry.py
uses for a dummy domain.
"""

from __future__ import annotations

import os
import re
from datetime import date

import pytest
import yaml

from datainsights import category_registry, domain_registry
from datainsights.correlation.hypothesis import assemble
from detection_engine.signal import Signal
from datainsights.fdm_worklist import RevenueModel, indicative_revenue_eur

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(REPO_ROOT, "config")


def _signal(signal_type="cash_buildup", domain="deposits", magnitude=3, party="P1") -> Signal:
    return Signal(
        prty_id=party, signal_type=signal_type, domain=domain, direction="increase",
        magnitude=magnitude, observed_date=date(2024, 6, 1), evidence_ref="TEST",
        source_tables=("TEST",), raw_measure={"current_balance": 500_000.0, "prior_balance": 100_000.0},
    )


@pytest.fixture
def fixture_registries(tmp_path, monkeypatch):
    """Extend (never replace) the real categories + domains config with
    fixture entries, point both registries at the copies, restore after."""
    cats = dict(category_registry._load())
    cats["categories"] = dict(cats["categories"])
    cats["categories"]["SUPPLY_CHAIN_FINANCE"] = {
        "label": "Supply Chain Finance", "description": "Likely needs receivables/payables financing",
        "colour": "#336699", "revenue_model": "financing",
        "revenue_mechanism": "Discount margin on financed invoices.",
        "talking_point": "Ask about supplier payment terms.",
    }
    cats["categories"]["ESG_ADVISORY"] = {
        "label": "ESG Advisory", "description": "Transition conversation, no product yet",
        "colour": "#669933", "revenue_model": "none",
    }
    cat_path = tmp_path / "categories.yaml"
    cat_path.write_text(yaml.safe_dump(cats))

    doms = dict(domain_registry._load())
    doms["trade"] = {
        "allowed_actions": ["RM to review", "No action -- monitor only"],
        "signals": {
            "supplier_concentration_rise": {"category": "SUPPLY_CHAIN_FINANCE",
                                            "hypothesis": "Supplier base is concentrating.",
                                            "why_now": "Concentration just crossed the threshold."},
            "esg_score_drop": {"category": "ESG_ADVISORY", "hypothesis": "ESG score fell.",
                               "non_revenue_action": "RM to open a transition conversation."},
            # HEDGING_NEED was declared everywhere and reachable from nowhere
            # (docs/hardcoding_audit.md). YAML alone makes it reachable:
            "fx_receivables_spike": {"category": "HEDGING_NEED", "hypothesis": "FX receivables jumped."},
        },
    }
    dom_path = tmp_path / "domains.yaml"
    dom_path.write_text(yaml.safe_dump(doms))

    monkeypatch.setattr(category_registry, "_DEFAULT_PATH", str(cat_path))
    monkeypatch.setattr(domain_registry, "_DEFAULT_PATH", str(dom_path))
    category_registry._load.cache_clear()
    domain_registry._load.cache_clear()
    try:
        yield
    finally:
        category_registry._load.cache_clear()
        domain_registry._load.cache_clear()


def _rules_model() -> RevenueModel:
    with open(os.path.join(CONFIG, "rules.yaml")) as f:
        return RevenueModel.from_rules_dict(yaml.safe_load(f))


def test_dummy_revenue_category_flows_through_assemble_and_revenue_model_from_yaml_only(fixture_registries):
    rec = assemble("P1", [_signal("supplier_concentration_rise", domain="trade")],
                   as_of=date(2024, 6, 30), high_risk_flag=False)
    assert rec is not None
    assert rec.nba_category == "SUPPLY_CHAIN_FINANCE"
    assert rec.hypothesis == "Supplier base is concentrating."
    # A revenue category with no per-signal sizing rule is honestly unsized,
    # but it is NOT the non-revenue branch and NOT the suppressed branch.
    assert rec.sizing_basis not in ("not_sized_non_revenue_category", "not_sized_suppressed_category")
    # Worklist revenue model: `revenue_model: financing` picks the formula.
    assert indicative_revenue_eur("SUPPLY_CHAIN_FINANCE", 100_000.0, _rules_model()) > 0
    assert category_registry.is_revenue("SUPPLY_CHAIN_FINANCE")
    assert category_registry.revenue_mechanism("SUPPLY_CHAIN_FINANCE").startswith("Discount margin")
    assert domain_registry.why_now_for("supplier_concentration_rise").startswith("Concentration")


def test_revenue_model_none_category_gets_no_sized_offer_and_its_own_action(fixture_registries):
    rec = assemble("P1", [_signal("esg_score_drop", domain="trade")],
                   as_of=date(2024, 6, 30), high_risk_flag=False)
    assert rec.nba_category == "ESG_ADVISORY"
    assert rec.sized_offer_eur is None
    assert rec.sizing_basis == "not_sized_non_revenue_category"
    assert rec.recommended_action == "RM to open a transition conversation."
    assert indicative_revenue_eur("ESG_ADVISORY", 100_000.0, _rules_model()) is None
    assert not category_registry.is_revenue("ESG_ADVISORY")


def test_high_risk_flag_suppresses_a_yaml_only_revenue_category_too(fixture_registries):
    """Rule 1 must be generic: it asks the registry whether the category
    earns revenue, not a literal set that a new category would miss."""
    rec = assemble("P1", [_signal("supplier_concentration_rise", domain="trade")],
                   as_of=date(2024, 6, 30), high_risk_flag=True)
    assert rec.nba_category == "ADVISORY_ONLY"
    assert rec.sizing_basis == "not_sized_suppressed_category"
    assert rec.recommended_action == category_registry.suppressed_action()


def test_hedging_need_becomes_reachable_with_a_yaml_signal_mapping_only(fixture_registries):
    rec = assemble("P1", [_signal("fx_receivables_spike", domain="trade")],
                   as_of=date(2024, 6, 30), high_risk_flag=False)
    assert rec.nba_category == "HEDGING_NEED"
    m = _rules_model()
    assert indicative_revenue_eur("HEDGING_NEED", 1_000_000.0, m) == round(1_000_000.0 * m.hedging_fee_pct, 2)


def test_dashboard_category_card_is_derived_from_the_registry_not_a_literal_dict():
    """The dashboard's CATEGORY_INFO must be built from
    category_registry.category_names() -- no category name may appear as
    a dict key literal in dashboard/app.py's module-level definitions."""
    dash = os.path.join(REPO_ROOT, "dashboard")
    files = [os.path.join(dash, "common.py"), os.path.join(dash, "app.py")] + \
            [os.path.join(dash, "tabs", f) for f in os.listdir(os.path.join(dash, "tabs")) if f.endswith(".py")]
    src = "\n".join(open(f).read() for f in files)
    assert "_category_registry.category_names()" in src
    for name in category_registry.category_names():
        assert not re.search(rf'^\s*"{name}"\s*:\s*\(', src, re.M), f"literal category card for {name}"


def test_every_category_referenced_in_any_config_is_declared_in_the_registry():
    """The one thing a registry buys you: a typo in domains_fdm.yaml or a
    legacy hypothesis file fails HERE, not silently as an unknown category
    in an RM's worklist."""
    declared = set(category_registry.category_names())
    referenced: dict[str, set[str]] = {}
    for fname in os.listdir(CONFIG):
        if not fname.endswith(".yaml") or fname == "categories.yaml":
            continue
        with open(os.path.join(CONFIG, fname)) as f:
            text = f.read()
        for m in re.finditer(r"^\s*category:\s*([A-Z_]+)\s*$", text, re.M):
            referenced.setdefault(fname, set()).add(m.group(1))
    assert referenced, "no config references a category -- regex broken?"
    for fname, cats in referenced.items():
        assert cats <= declared, f"{fname} references undeclared categories: {sorted(cats - declared)}"
    # And the built-in fallbacks the assembler itself relies on exist.
    assert {"ADVISORY_ONLY", "RISK_REVIEW"} <= declared


def test_revenue_model_values_are_validated():
    assert set(category_registry.VALID_REVENUE_MODELS) == {"financing", "treasury", "hedging", "none"}
    for name in category_registry.category_names():
        assert category_registry.revenue_model(name) in category_registry.VALID_REVENUE_MODELS


def test_unknown_category_is_an_error_not_a_silent_default():
    with pytest.raises(category_registry.UnknownCategoryError):
        category_registry.revenue_model("NOT_A_CATEGORY")
