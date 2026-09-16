"""
The four ML challenger slot interfaces from docs/decision_record.md Tab 6
(Slot Type E), plus the deterministic default implementation of each.

Signatures are taken verbatim from the decision record so a future
implementation written against that document drops in without
translation:

    class MagnitudeNormaliser(Protocol):   # SLOT E1
        def normalise(self, raw, ctx) -> float
    class BaselineModel(Protocol):         # SLOT E2  <- highest ML value
        def expected(self, prty_id, metric, as_at) -> tuple[float, float]
    class ExposureQualifier(Protocol):     # SLOT E3
        def qualifies(self, prty_id, event) -> tuple[bool, float]
    class PropensityModel(Protocol):       # SLOT E4
        def score(self, prty_id, category) -> float

The sklearn-backed E2 challenger lives in datainsights/ml/baselines.py so
this module imports nothing heavier than the standard library + the
Protocol definitions -- code that only needs the interface never pays for
scikit-learn.
"""

from __future__ import annotations

import statistics
from datetime import date
from typing import Protocol, runtime_checkable


@runtime_checkable
class MagnitudeNormaliser(Protocol):
    """SLOT E1. Turns a domain-native raw measure into the normalised
    0..1 magnitude the Signal contract requires, so magnitudes are
    comparable across domains in the correlation layer."""

    def normalise(self, raw: float, ctx: dict) -> float: ...


@runtime_checkable
class BaselineModel(Protocol):
    """SLOT E2 -- the decision record's "best ML value": "£5m from a
    £2m-turnover client vs a £500m one are different events. Turns a
    threshold into an anomaly score."

    Returns (expected_value, tolerance) for one client's one metric as at
    a date. A detector compares an observation against this instead of
    against a flat, book-wide threshold."""

    def expected(self, prty_id: str, metric: str, as_at: date) -> tuple[float, float]: ...


@runtime_checkable
class ExposureQualifierModel(Protocol):
    """SLOT E3. The deterministic implementation of this already exists
    and is in production use for this build:
    external_events/exposure_qualifier.py's `qualifies`. The decision
    record's note -- "Supervised once labels exist" -- is why there is no
    ML version here: the labels (RM responses) are blocked on access, per
    D6. Defining the interface now means the swap is later a wiring
    change."""

    def qualifies(self, prty_id: str, event) -> tuple[bool, float]: ...


@runtime_checkable
class PropensityModel(Protocol):
    """SLOT E4. Decision record: "Close to what Pega adaptive models do.
    Coordinate, do not compete." Label would be the RM response taxonomy
    (Customer Engaged / Not Appropriate / Remind Me Later)."""

    def score(self, prty_id: str, category: str) -> float: ...


# ---------------------------------------------------------------------
# Deterministic defaults -- the production path, and the benchmark any
# challenger has to beat.
# ---------------------------------------------------------------------

class ProportionalNormaliser:
    """E1 default: raw / saturation_at, clamped to 0..1. Explicit,
    auditable, no training -- exactly what the detectors' inline
    `clamp_magnitude(x / saturation)` calls already do today, lifted
    behind the interface so a learnable version can replace it without
    touching any detector."""

    def __init__(self, saturation_at: float):
        if saturation_at <= 0:
            raise ValueError("saturation_at must be positive")
        self.saturation_at = saturation_at

    def normalise(self, raw: float, ctx: dict | None = None) -> float:
        return max(0.0, min(1.0, float(raw) / self.saturation_at))


class DeterministicBaseline:
    """E2 default: per-client median + MAD over that client's OWN prior
    observations. Client-specific by construction (the decision record's
    core objection to flat thresholds), auditable, and trains on nothing
    -- it is a statistic, not a model.

    Deliberately the same median/MAD technique
    detection_engine/large_incoming_payment.py already uses and tests, so
    the "auditable baseline stays as the challenger benchmark" claim is
    true rather than aspirational: this IS the shipped behaviour.

    `history` maps (prty_id, metric) -> list of (date, value), supplied by
    the caller from whatever DataSource is wired -- this class never
    reads data itself, so it works identically against local CSVs or
    Snowflake."""

    def __init__(self, history: dict[tuple[str, str], list[tuple[date, float]]],
                 min_observations: int = 3):
        self.history = history
        self.min_observations = min_observations

    def expected(self, prty_id: str, metric: str, as_at: date) -> tuple[float, float]:
        observations = [
            value for obs_date, value in self.history.get((prty_id, metric), [])
            if obs_date <= as_at  # strictly no future leakage -- as-at discipline
        ]
        if len(observations) < self.min_observations:
            raise InsufficientHistory(
                f"{prty_id}/{metric} has {len(observations)} observation(s) at "
                f"{as_at}, need {self.min_observations}. Callers must treat this "
                f"as 'insufficient_evidence', never as 'expected value is zero'."
            )
        median = statistics.median(observations)
        mad = statistics.median([abs(v - median) for v in observations])
        return float(median), float(mad)


class UnavailablePropensityModel:
    """E4 default: raises. There is no propensity model and there cannot
    honestly be one yet -- its label is the RM response taxonomy, which
    docs/decision_record.md D6 records as "Blocked on access, not data."

    Raising beats returning 0.5: a neutral-looking score would silently
    flow into a worklist and look like a real model output."""

    def score(self, prty_id: str, category: str) -> float:
        raise NotImplementedError(
            "SLOT E4: no propensity model exists. Its training label is the RM "
            "response taxonomy (Customer Engaged / Not Appropriate / Remind Me "
            "Later), which per docs/decision_record.md D6 is blocked on ACCESS, "
            "not availability -- the data exists in Pega/CRM, this project "
            "cannot read it. Also note the record's warning: 'Coordinate, do "
            "not compete' with Pega's adaptive models."
        )


class InsufficientHistory(Exception):
    """Raised by a BaselineModel when a client has too little of their own
    history to have a meaningful 'normal'. Callers must surface this as
    insufficient_evidence rather than substituting a book-wide default --
    substituting one is exactly the flat-threshold behaviour SLOT E2
    exists to replace."""
