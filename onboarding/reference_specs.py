"""
The existing hand-written detectors, expressed as archetype specs.

These are NOT a replacement for detection_engine/*.py -- those modules
are untouched and remain the production detectors. These specs exist for
two narrow purposes:

  1. NOVELTY BASELINE. onboarding/signal_screener.py measures whether a
     discovered candidate flags clients the bank does not already flag.
     That needs each existing detector's flagged-client set, computed the
     same way the candidates' are, so the Jaccard comparison is apples to
     apples.

  2. ARCHETYPE EVIDENCE. If every hand-written detector can be written as
     one of the five archetypes, the archetype vocabulary is adequate --
     and a discovered candidate cannot express anything the existing
     detectors could not. That is a claim worth being able to check.

IMPORTANT -- these are APPROXIMATIONS, deliberately so. The real
detectors carry per-currency floors, cooldown bookkeeping, SLOT E2
baseline switching, and `insufficient_evidence` rows that these specs do
not reproduce. Parameters here come from config/rules.yaml where they map
cleanly and are otherwise the archetype defaults. Using them as a
novelty baseline is sound (an over-broad baseline only makes the novelty
test stricter); using them AS detectors would not be, and nothing does.
"""

from __future__ import annotations

import os
from functools import lru_cache

import yaml

from detection_engine.specs.archetypes import SignalSpec, SpecError

_RULES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "rules.yaml"
)


@lru_cache(maxsize=1)
def _rules() -> dict:
    try:
        with open(_RULES) as f:
            return yaml.safe_load(f) or {}
    except OSError:
        return {}


def _param(detector: str, key: str, default):
    return (_rules().get(detector) or {}).get(key, default)


@lru_cache(maxsize=1)
def reference_specs() -> tuple[SignalSpec, ...]:
    """Every hand-written detector as a spec. A detector that cannot be
    expressed raises at import of this module rather than being silently
    omitted -- a silent omission would weaken the novelty baseline."""
    specs = [
        # --- deposits -----------------------------------------------------
        SignalSpec(
            signal_type="cash_buildup", archetype="own_history_deviation",
            domain="deposits", concept="BalanceObservation",
            grain=("party_id", "account_id"),
            params={"measure": "balance", "date_field": "observed_at",
                    "window_days": int(_param("cash_buildup", "window_days", 30)),
                    "k": 2.0, "min_observations": 8, "median_mode": "exact",
                    "cooldown_days": int(_param("cash_buildup", "cooldown_days", 30))}),
        SignalSpec(
            signal_type="large_incoming_payment", archetype="own_history_deviation",
            domain="deposits", concept="Transaction", grain=("party_id", "account_id"),
            params={"measure": "amount", "date_field": "posted_at",
                    "window_days": 90, "k": float(_param("large_incoming_payment", "mad_multiple", 2.1)),
                    "min_observations": 8, "median_mode": "exact",
                    "cooldown_days": int(_param("large_incoming_payment", "cooldown_days", 14))}),
        SignalSpec(
            signal_type="revenue_pattern_change", archetype="own_history_deviation",
            domain="deposits", concept="Transaction", grain=("party_id", "account_id"),
            params={"measure": "amount", "date_field": "posted_at",
                    "window_days": 60, "k": 1.8, "min_observations": 8,
                    "median_mode": "exact"}),
        SignalSpec(
            signal_type="dormancy", archetype="absence", domain="deposits",
            concept="Transaction", grain=("party_id", "account_id"), direction="decrease",
            params={"date_field": "posted_at",
                    "silent_days": int(_param("dormancy", "silent_days", 90))}),

        # --- lending ------------------------------------------------------
        SignalSpec(
            signal_type="facility_utilization_spike", archetype="ratio_threshold",
            domain="lending", concept="BalanceObservation", grain=("party_id", "account_id"),
            params={"numerator": "balance", "denominator": "original_limit",
                    "date_field": "observed_at",
                    "threshold": float(_param("facility_utilization_spike", "utilization_threshold", 0.85)),
                    "comparison": "gte",
                    "cooldown_days": int(_param("facility_utilization_spike", "cooldown_days", 30))}),
        SignalSpec(
            signal_type="facility_maturity_approaching", archetype="date_proximity",
            domain="lending", concept="Account", grain=("party_id", "account_id"),
            params={"date_field": "close_date",
                    "within_days": int(_param("facility_maturity_approaching", "within_days", 90))}),
        SignalSpec(
            signal_type="fixed_rate_expiry", archetype="date_proximity", domain="lending",
            concept="Account", grain=("party_id", "account_id"),
            params={"date_field": "fixed_rate_end_date",
                    "within_days": int(_param("fixed_rate_expiry", "within_days", 90))}),
        SignalSpec(
            signal_type="collateral_coverage_drop", archetype="ratio_threshold",
            domain="lending", concept="CollateralValuation", grain=("party_id", "account_id"),
            direction="decrease",
            params={"numerator": "valuation_amount", "denominator": "original_limit",
                    "date_field": "valued_at",
                    "threshold": float(_param("collateral_coverage_drop", "coverage_threshold", 1.0)),
                    "comparison": "lte"}),

        # --- risk ---------------------------------------------------------
        SignalSpec(
            signal_type="rating_downgrade", archetype="ordinal_migration", domain="risk",
            concept="RiskGradeVersion", grain=("party_id",), direction="decrease",
            params={"field": "risk_grade", "date_field": "valid_from",
                    "order": list(_param("rating_downgrade", "grade_order",
                                          ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D"])),
                    "min_steps": int(_param("rating_downgrade", "min_steps", 1))}),
    ]
    return tuple(specs)


def archetype_coverage() -> dict[str, list[str]]:
    """Which hand-written detectors map to which archetype -- the evidence
    for the claim that five shapes cover the existing nine."""
    out: dict[str, list[str]] = {}
    for spec in reference_specs():
        out.setdefault(spec.archetype, []).append(spec.signal_type)
    return out


__all__ = ["reference_specs", "archetype_coverage", "SpecError"]
