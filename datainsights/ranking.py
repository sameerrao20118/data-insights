"""
Baseline, auditable ranking -- explicit formula over available evidence,
magnitude, and recency. No ML here; see project instructions section 7:
"Start with an explicit, auditable ranking formula ... Preserve components
and explain each recommendation."

Answers only "how large is the potential opportunity, given evidence
already established by the detector" -- NOT "did an event occur" (that's
the detector's job) and NOT "will outreach cause incremental value" (no
causal/outcome label exists in this dataset to support that claim; see
docs/detector_spec_large_incoming_payment.md and project instructions
section 7's four-questions framing).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RankingConfig:
    magnitude_weight: float
    recency_weight: float
    recency_half_life_days: float
    magnitude_saturation_at: float

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "RankingConfig":
        r = rules["ranking"]
        return cls(
            magnitude_weight=r["magnitude_weight"],
            recency_weight=r["recency_weight"],
            recency_half_life_days=r["recency_half_life_days"],
            magnitude_saturation_at=r["magnitude_saturation_at"],
        )


def rank(detections: pd.DataFrame, as_of: date, config: RankingConfig) -> pd.DataFrame:
    """Only ranks rows with status == 'detected' (not insufficient_evidence
    or suppressed_cooldown -- those aren't active recommendations). Adds
    magnitude_component, recency_component, score, and rank (1 = highest)."""
    active = detections[detections["status"] == "detected"].copy()
    if active.empty:
        active["magnitude_component"] = []
        active["recency_component"] = []
        active["score"] = []
        active["rank"] = []
        return active

    baseline_median = active["baseline_median"].astype(float)
    baseline_mad = active["baseline_mad"].astype(float).replace(0, np.nan)
    mad_multiples = (active["flagged_amount"].astype(float) - baseline_median) / baseline_mad
    mad_multiples = mad_multiples.fillna(active["threshold_multiplier"].astype(float))  # mad==0 edge case
    active["magnitude_component"] = (mad_multiples / config.magnitude_saturation_at).clip(upper=1.0)

    event_dates = pd.to_datetime(active["event_date"]).dt.date
    age_days = event_dates.apply(lambda d: (as_of - d).days).clip(lower=0)
    active["recency_component"] = 0.5 ** (age_days / config.recency_half_life_days)

    active["score"] = (
        config.magnitude_weight * active["magnitude_component"]
        + config.recency_weight * active["recency_component"]
    )
    active = active.sort_values("score", ascending=False).reset_index(drop=True)
    active["rank"] = active.index + 1
    return active
