"""
R10 (docs/refactor_plan.md) -- cross-domain rules. Before this, two
signals from two domains only earned +1 confidence; the strongest single
signal always picked the category. Now config/domains_*.yaml's
`combinations:` block is consulted first, and a combination with no rule
falls through unchanged.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml

from datainsights import domain_registry
from datainsights.correlation.hypothesis import assemble
from detection_engine.signal import Signal

AS_OF = date(2024, 6, 30)


def _sig(signal_type, domain, magnitude, raw=None):
    return Signal(prty_id="P1", signal_type=signal_type, domain=domain, direction="increase",
                  magnitude=magnitude, observed_date=date(2024, 6, 1), evidence_ref=f"TEST:{signal_type}",
                  source_tables=("TEST",), raw_measure=raw or {})


CASH = _sig("cash_buildup", "deposits", 4.0, {"current_balance": 900_000.0, "prior_balance": 300_000.0})
UTIL = _sig("facility_utilization_spike", "lending", 2.0, {"drawn_amount": 950_000.0, "orig_limit": 1_000_000.0})
MATURITY = _sig("facility_maturity_approaching", "lending", 1.0,
                {"orig_limit": 500_000.0, "close_date": "2024-08-01", "days_to_close": 32})
DORMANCY = _sig("dormancy", "deposits", 1.0)
DOWNGRADE = _sig("rating_downgrade", "risk", 2.0)


def test_same_strongest_signal_two_different_categories_depending_on_what_else_fired():
    """The audit's two examples (docs/hardcoding_audit.md §6): balance
    rising ALONE is idle surplus (treasury); balance rising WITH the
    facility drawn harder is growth outrunning working capital (financing)."""
    alone = assemble("P1", [CASH], as_of=AS_OF, high_risk_flag=False)
    with_util = assemble("P1", [CASH, UTIL], as_of=AS_OF, high_risk_flag=False)
    assert alone.nba_category == "TREASURY_OPPORTUNITY"
    assert alone.combination_rule is None
    assert with_util.nba_category == "FINANCING_NEED"
    assert with_util.combination_rule == "growth_outrunning_working_capital"
    assert with_util.endogenous_signal_type == "cash_buildup"  # strongest is still the strongest
    assert "working capital" in with_util.hypothesis


def test_size_from_sizes_the_offer_from_the_named_signal_not_the_strongest():
    rec = assemble("P1", [CASH, UTIL], as_of=AS_OF, high_risk_flag=False)
    # facility_utilization_spike's own sizing: limit increase to 70% utilisation
    assert rec.sizing_basis.startswith("limit_increase_to_")
    assert rec.sized_offer_eur == round(950_000.0 / 0.70 - 1_000_000.0, 2)


def test_a_combination_with_no_rule_falls_through_unchanged():
    rec = assemble("P1", [CASH, DORMANCY], as_of=AS_OF, high_risk_flag=False)
    assert rec.combination_rule is None
    assert rec.nba_category == "TREASURY_OPPORTUNITY"
    assert rec.sizing_basis == "balance_buildup_amount_illustrative"


def test_risk_suppression_still_applies_after_a_rule_fires():
    rec = assemble("P1", [CASH, UTIL, DOWNGRADE], as_of=AS_OF, high_risk_flag=False)
    assert rec.combination_rule == "growth_outrunning_working_capital"
    assert rec.nba_category == "ADVISORY_ONLY"
    assert rec.sizing_basis == "not_sized_suppressed_category"
    flagged = assemble("P1", [CASH, UTIL], as_of=AS_OF, high_risk_flag=True)
    assert flagged.nba_category == "ADVISORY_ONLY"


def test_most_specific_rule_wins(tmp_path, monkeypatch):
    doms = dict(domain_registry._load())
    doms["combinations"] = [
        {"name": "pair", "when": ["cash_buildup", "facility_utilization_spike"],
         "category": "FINANCING_NEED", "hypothesis": "pair"},
        {"name": "triple", "when": ["cash_buildup", "facility_utilization_spike", "facility_maturity_approaching"],
         "category": "CAPEX_FINANCING", "hypothesis": "triple"},
    ]
    path = tmp_path / "domains.yaml"
    path.write_text(yaml.safe_dump(doms))
    monkeypatch.setattr(domain_registry, "_DEFAULT_PATH", str(path))
    domain_registry._load.cache_clear()
    try:
        assert domain_registry.matching_combination({"cash_buildup", "facility_utilization_spike"}).name == "pair"
        assert domain_registry.matching_combination(
            {"cash_buildup", "facility_utilization_spike", "facility_maturity_approaching"}).name == "triple"
        rec = assemble("P1", [CASH, UTIL, MATURITY], as_of=AS_OF, high_risk_flag=False)
        assert rec.nba_category == "CAPEX_FINANCING" and rec.combination_rule == "triple"
    finally:
        domain_registry._load.cache_clear()


def test_combinations_key_is_not_a_domain_and_rules_are_validated():
    assert "combinations" not in domain_registry.domain_names()
    assert "combinations" not in domain_registry._all_signals()
    for rule in domain_registry.combination_rules():
        assert len(rule.when) >= 2, rule.name
        for sig in rule.when:
            assert sig in domain_registry._all_signals(), f"{rule.name}: unknown signal {sig}"
        if rule.size_from:
            assert rule.size_from in rule.when


def test_malformed_rule_is_rejected(tmp_path, monkeypatch):
    doms = dict(domain_registry._load())
    doms["combinations"] = [{"name": "broken", "when": ["cash_buildup", "dormancy"]}]
    path = tmp_path / "domains.yaml"
    path.write_text(yaml.safe_dump(doms))
    monkeypatch.setattr(domain_registry, "_DEFAULT_PATH", str(path))
    domain_registry._load.cache_clear()
    try:
        with pytest.raises(ValueError, match="broken"):
            domain_registry.combination_rules()
    finally:
        domain_registry._load.cache_clear()
