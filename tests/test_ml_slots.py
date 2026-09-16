"""
Tests for datainsights/ml/ -- the Slot Type E interfaces, their
deterministic defaults, and the opt-in IsolationForest challenger.

The honesty tests matter most: too little history must raise rather than
return a plausible number, future observations must never leak into a
baseline, and the propensity slot must refuse rather than emit a neutral-
looking fake score.
"""

from datetime import date, timedelta

import pytest

from datainsights.ml import (
    BaselineModel,
    DeterministicBaseline,
    InsufficientHistory,
    MagnitudeNormaliser,
    PropensityModel,
    ProportionalNormaliser,
    UnavailablePropensityModel,
)
from datainsights.ml.baselines import IsolationForestBaseline

START = date(2025, 1, 1)


def series(values, start=START, step_days=7):
    return [(start + timedelta(days=i * step_days), float(v)) for i, v in enumerate(values)]


# --- interfaces ---------------------------------------------------------

def test_defaults_satisfy_their_protocols():
    assert isinstance(ProportionalNormaliser(1.0), MagnitudeNormaliser)
    assert isinstance(DeterministicBaseline({}), BaselineModel)
    assert isinstance(IsolationForestBaseline({}), BaselineModel)
    assert isinstance(UnavailablePropensityModel(), PropensityModel)


# --- E1 -----------------------------------------------------------------

def test_normaliser_clamps_to_unit_interval():
    n = ProportionalNormaliser(saturation_at=0.5)
    assert n.normalise(0.25, {}) == 0.5
    assert n.normalise(10.0, {}) == 1.0
    assert n.normalise(-1.0, {}) == 0.0


def test_normaliser_rejects_nonpositive_saturation():
    with pytest.raises(ValueError):
        ProportionalNormaliser(0)


# --- E2 deterministic ---------------------------------------------------

def test_deterministic_baseline_is_client_specific():
    """The decision record's core point: the same absolute amount is normal
    for one client and anomalous for another."""
    history = {
        ("SMALL", "credit"): series([1000, 1100, 900, 1050, 950]),
        ("LARGE", "credit"): series([500_000, 510_000, 490_000, 505_000, 495_000]),
    }
    baseline = DeterministicBaseline(history)
    small_median, _ = baseline.expected("SMALL", "credit", START + timedelta(days=100))
    large_median, _ = baseline.expected("LARGE", "credit", START + timedelta(days=100))
    assert small_median == 1000
    assert large_median == 500_000


def test_deterministic_baseline_never_uses_future_observations():
    history = {("C1", "credit"): series([100, 100, 100, 1_000_000, 1_000_000])}
    baseline = DeterministicBaseline(history)
    # as-at the 3rd observation, the two later huge values must not be visible
    median, mad = baseline.expected("C1", "credit", START + timedelta(days=14))
    assert median == 100
    assert mad == 0


def test_deterministic_baseline_raises_on_insufficient_history():
    baseline = DeterministicBaseline({("C1", "credit"): series([100, 200])}, min_observations=3)
    with pytest.raises(InsufficientHistory):
        baseline.expected("C1", "credit", START + timedelta(days=100))


def test_unknown_client_raises_rather_than_defaulting():
    """Substituting a book-wide default here would reintroduce exactly the
    flat threshold SLOT E2 exists to replace."""
    with pytest.raises(InsufficientHistory):
        DeterministicBaseline({}).expected("NOBODY", "credit", START)


# --- E2 challenger ------------------------------------------------------

def test_isolation_forest_excludes_a_past_spike_from_normal():
    """What the challenger is FOR: a one-off historical spike should not
    widen the client's tolerance band the way it does under plain MAD."""
    values = [1000, 1020, 980, 1010, 990, 1005, 995, 1015, 50_000, 1000, 1008, 992]
    history = {("C1", "credit"): series(values)}
    as_at = START + timedelta(days=7 * len(values))

    det_median, det_mad = DeterministicBaseline(history).expected("C1", "credit", as_at)
    iso_median, iso_mad = IsolationForestBaseline(history).expected("C1", "credit", as_at)

    assert abs(iso_median - 1000) < 20
    assert iso_mad <= det_mad, "challenger band should not be wider than the contaminated MAD band"


def test_isolation_forest_needs_more_history_than_deterministic():
    history = {("C1", "credit"): series([100, 110, 90, 105, 95])}
    DeterministicBaseline(history).expected("C1", "credit", START + timedelta(days=100))
    with pytest.raises(InsufficientHistory):
        IsolationForestBaseline(history).expected("C1", "credit", START + timedelta(days=100))


def test_isolation_forest_respects_as_at():
    values = [100] * 10 + [1_000_000] * 10
    history = {("C1", "credit"): series(values)}
    median, _ = IsolationForestBaseline(history).expected("C1", "credit", START + timedelta(days=7 * 9))
    assert median == 100


# --- E4 -----------------------------------------------------------------

def test_propensity_slot_refuses_rather_than_faking_a_score():
    with pytest.raises(NotImplementedError, match="blocked on ACCESS"):
        UnavailablePropensityModel().score("PRTY00001", "FINANCING_NEED")
