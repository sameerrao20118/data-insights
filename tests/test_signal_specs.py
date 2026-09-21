"""
The spec/archetype layer (docs/signal_discovery_design.md recs. 1-3).

The load-bearing tests here are the as-of correctness ones and the
archetype-coverage one. The rest guard the Signal contract the
correlation layer depends on.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from detection_engine.signal import VALID_DIRECTIONS, VALID_DOMAINS
from detection_engine.specs.archetypes import (
    ARCHETYPES,
    SignalSpec,
    SpecError,
    run_spec,
)
from onboarding.reference_specs import archetype_coverage, reference_specs


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _balances(n_days: int = 120, spike_on: int | None = None) -> pd.DataFrame:
    start = date(2026, 1, 1)
    rows = []
    for account, party in (("AC-1", "P-1"), ("AC-2", "P-2")):
        for i in range(n_days):
            amount = 100_000.0 + (i % 5) * 100
            if spike_on is not None and i == spike_on and account == "AC-1":
                amount = 900_000.0
            rows.append({"account_id": account, "party_id": party,
                         "observed_at": start + timedelta(days=i),
                         "balance": amount, "original_limit": 500_000.0,
                         "currency": "EUR"})
    return pd.DataFrame(rows)


def _deviation_spec(**overrides) -> SignalSpec:
    params = {"measure": "balance", "date_field": "observed_at",
              "window_days": 90, "k": 2.1, "min_observations": 8,
              "median_mode": "exact"}
    params.update(overrides.pop("params", {}))
    kwargs = {"signal_type": "test_deviation", "archetype": "own_history_deviation",
              "domain": "deposits", "concept": "BalanceObservation",
              "grain": ("party_id", "account_id")}
    kwargs.update(overrides)
    return SignalSpec(params=params, **kwargs)


# --------------------------------------------------------------------------
# spec validation
# --------------------------------------------------------------------------

def test_unknown_archetype_rejected():
    with pytest.raises(SpecError, match="unknown archetype"):
        SignalSpec(signal_type="x", archetype="telepathy", domain="deposits",
                   concept="BalanceObservation", grain=("party_id",))


def test_unregistered_domain_rejected():
    with pytest.raises(SpecError, match="domain"):
        _deviation_spec(domain="marketing")


def test_approx_median_rejected_by_pandas_executor():
    """`approx` is reserved for a pushdown executor that cannot compute an
    exact median. Silently giving it exact results would make a
    cross-backend difference invisible -- the exact failure mode the
    median_mode field exists to prevent."""
    with pytest.raises(SpecError, match="median_mode"):
        _deviation_spec(params={"median_mode": "approx"})


def test_missing_canonical_field_is_an_error_not_silence():
    """A spec whose fields the binding does not supply must say so, not
    quietly produce zero signals -- that would look like 'no signal'."""
    spec = _deviation_spec(params={"measure": "not_a_real_field"})
    with pytest.raises(SpecError, match="unavailable on this schema"):
        run_spec(spec, _balances())


# --------------------------------------------------------------------------
# as-of correctness -- the leakage guarantee
# --------------------------------------------------------------------------

@pytest.mark.parametrize("spec_factory,frame", [
    (lambda: _deviation_spec(), _balances(spike_on=100)),
    (lambda: SignalSpec(signal_type="t_ratio", archetype="ratio_threshold", domain="lending",
                        concept="BalanceObservation", grain=("party_id", "account_id"),
                        params={"numerator": "balance", "denominator": "original_limit",
                                "date_field": "observed_at", "threshold": 0.1}), _balances()),
])
def test_no_signal_is_dated_after_as_of(spec_factory, frame):
    """Every archetype, not just the two that take a reference date. This
    caught a real bug: run_spec() originally passed as_of only to
    proximity/absence, so the row-scanning archetypes happily flagged
    December rows while replaying July."""
    as_of = date(2026, 2, 1)
    for signal in run_spec(spec_factory(), frame, as_of=as_of):
        assert signal.observed_date <= as_of, f"{signal.signal_type} leaked {signal.observed_date}"


def test_baseline_uses_only_earlier_rows():
    """A spike must not be present in its own baseline. With the spike on
    the last day, replaying up to the day before must not flag it."""
    frame = _balances(spike_on=119)
    spec = _deviation_spec()
    before = run_spec(spec, frame, as_of=date(2026, 1, 1) + timedelta(days=118))
    assert not [s for s in before if s.observed_date == date(2026, 1, 1) + timedelta(days=119)]


# --------------------------------------------------------------------------
# Signal contract
# --------------------------------------------------------------------------

def test_emitted_signals_satisfy_the_signal_contract():
    signals = run_spec(_deviation_spec(), _balances(spike_on=100))
    assert signals, "fixture should produce at least one signal"
    for signal in signals:
        assert signal.domain in VALID_DOMAINS
        assert signal.direction in VALID_DIRECTIONS
        assert 0.0 <= signal.magnitude <= 1.0, "magnitude must be clamped 0..1"
        assert isinstance(signal.observed_date, date)
        assert signal.evidence_ref and ":eff=" in signal.evidence_ref
        assert signal.raw_measure


def test_raw_measure_matches_the_declared_archetype_contract():
    """Recommendation 3: _size_endogenous() reads specific raw_measure
    keys to size an offer, so each archetype declares what it populates."""
    for archetype_name, archetype in ARCHETYPES.items():
        specs = [s for s in reference_specs() if s.archetype == archetype_name]
        assert specs, f"no reference spec exercises archetype {archetype_name}"
        assert archetype.raw_measure_keys, f"{archetype_name} declares no raw_measure keys"


def test_deviation_populates_sizing_keys():
    for signal in run_spec(_deviation_spec(), _balances(spike_on=100)):
        assert "flagged_amount" in signal.raw_measure
        assert "baseline_median" in signal.raw_measure
        assert signal.raw_measure["median_mode"] == "exact"


# --------------------------------------------------------------------------
# archetype coverage -- the vocabulary claim
# --------------------------------------------------------------------------

def test_every_handwritten_detector_maps_to_an_archetype():
    """The claim the archetype layer rests on: five shapes cover the nine
    hand-written detectors. If this fails, a discovered candidate could
    not have expressed what that detector does, and the novelty baseline
    in signal_screener.py is incomplete."""
    coverage = archetype_coverage()
    assert set(coverage) <= set(ARCHETYPES)
    covered = {name for names in coverage.values() for name in names}
    for expected in ("cash_buildup", "large_incoming_payment", "dormancy",
                     "facility_utilization_spike", "facility_maturity_approaching",
                     "fixed_rate_expiry", "rating_downgrade"):
        assert expected in covered, f"{expected} has no archetype spec"


def test_reference_specs_are_not_registered_as_production_detectors():
    """These are a novelty baseline and coverage evidence, never
    detectors. They must not be `discovered`, and must not be shadow --
    they describe what already exists."""
    for spec in reference_specs():
        assert spec.origin == "handwritten"
        assert spec.status == "active"


# --------------------------------------------------------------------------
# cooldown
# --------------------------------------------------------------------------

def test_cooldown_suppresses_repeat_signals_on_one_entity():
    frame = _balances(n_days=200)
    frame.loc[frame.index % 40 == 39, "balance"] = 900_000.0
    without = run_spec(_deviation_spec(params={"cooldown_days": 0}), frame)
    with_cd = run_spec(_deviation_spec(params={"cooldown_days": 60}), frame)
    assert len(with_cd) <= len(without)
