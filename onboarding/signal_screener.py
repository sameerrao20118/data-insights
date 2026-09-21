"""
Stage B of signal discovery (docs/signal_discovery_design.md rec. 5):
screen enumerated candidates EMPIRICALLY, still with no LLM.

The honest framing, restated because it is the crux of the whole design:
without RM outcome labels you cannot measure whether a pattern is
VALUABLE. You can measure whether it is NOVEL -- i.e. whether it flags a
set of clients that existing signals do not already flag. So that is all
this stage claims to do.

Four tests, every one of them deterministic:

  1. FIRES      -- flags a meaningful minority. A candidate that fires on
                   nobody is useless; one that fires on most of the book
                   is a threshold, not a signal.
  2. STABLE     -- fires at a comparable rate across several as-of dates.
                   A candidate that only fires on one date is an artefact.
  3. NOVEL      -- low Jaccard overlap with every existing signal's
                   flagged-client set. This is the real test.
  4. AS-OF SAFE -- run under the same as-of discipline the detectors use;
                   a candidate that needs future rows is dropped.

A candidate passing all four is a candidate worth ASKING AN LLM to name
and explain (Stage C). It is not yet a signal, and it is certainly not
yet a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from detection_engine.signal import Signal
from detection_engine.specs.archetypes import SignalSpec, SpecError, run_spec
from onboarding.signal_enumerator import Candidate

# A candidate must flag at least this share of entities, and at most this
# share. Outside the band it is noise or a tautology.
MIN_FIRE_RATE = 0.01
MAX_FIRE_RATE = 0.40

# Above this Jaccard overlap with an existing signal, a candidate is
# telling us something we already detect.
MAX_OVERLAP = 0.60

# Fire rate must not swing more than this (absolute) across as-of dates.
MAX_RATE_SWING = 0.25


@dataclass
class ScreenResult:
    signal_type: str
    passed: bool
    fire_rate: float = 0.0
    rate_swing: float = 0.0
    max_overlap: float = 0.0
    overlaps_with: str = ""
    n_flagged: int = 0
    as_of_dates: tuple[str, ...] = ()
    rejected_reason: str = ""
    flagged_parties: frozenset = field(default_factory=frozenset)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _flagged(signals: list[Signal]) -> frozenset:
    return frozenset(s.prty_id for s in signals)


def screen_candidate(candidate: Candidate, frames: dict[str, pd.DataFrame],
                     as_of_dates: list[date],
                     existing: dict[str, frozenset]) -> ScreenResult:
    """Run one candidate across several as-of dates and judge it."""
    spec = candidate.spec
    df = frames.get(candidate.concept)
    if df is None or df.empty:
        return ScreenResult(spec.signal_type, False,
                            rejected_reason=f"no frame for concept {candidate.concept!r}")

    total_entities = df["party_id"].nunique() if "party_id" in df.columns else 0
    if not total_entities:
        return ScreenResult(spec.signal_type, False, rejected_reason="frame has no party_id")

    per_date_rates, union_flagged = [], set()
    for as_of in as_of_dates:
        try:
            signals = run_spec(spec, df, as_of=as_of)
        except SpecError as e:
            return ScreenResult(spec.signal_type, False, rejected_reason=f"spec error: {e}")
        except Exception as e:  # noqa: BLE001 -- a candidate that crashes is simply not a candidate
            return ScreenResult(spec.signal_type, False,
                                rejected_reason=f"execution failed: {type(e).__name__}: {e}")
        # As-of safety: the executors already refuse future rows, but assert
        # it here too rather than trusting an upstream guarantee.
        leaked = [s for s in signals if s.observed_date > as_of]
        if leaked:
            return ScreenResult(spec.signal_type, False,
                                rejected_reason=f"{len(leaked)} signal(s) observed after as_of {as_of}")
        flagged = _flagged(signals)
        union_flagged |= flagged
        per_date_rates.append(len(flagged) / total_entities)

    fire_rate = sum(per_date_rates) / len(per_date_rates) if per_date_rates else 0.0
    rate_swing = (max(per_date_rates) - min(per_date_rates)) if per_date_rates else 0.0
    union = frozenset(union_flagged)

    base = ScreenResult(
        signal_type=spec.signal_type, passed=False, fire_rate=fire_rate,
        rate_swing=rate_swing, n_flagged=len(union),
        as_of_dates=tuple(d.isoformat() for d in as_of_dates), flagged_parties=union,
    )

    if fire_rate < MIN_FIRE_RATE:
        base.rejected_reason = f"fires on {fire_rate:.1%} of entities, below {MIN_FIRE_RATE:.0%}"
        return base
    if fire_rate > MAX_FIRE_RATE:
        base.rejected_reason = (f"fires on {fire_rate:.1%} of entities, above {MAX_FIRE_RATE:.0%} "
                                f"-- a threshold, not a signal")
        return base
    if rate_swing > MAX_RATE_SWING:
        base.rejected_reason = f"fire rate swings {rate_swing:.1%} across as-of dates -- unstable"
        return base

    worst_overlap, worst_name = 0.0, ""
    for name, existing_set in existing.items():
        overlap = _jaccard(union, existing_set)
        if overlap > worst_overlap:
            worst_overlap, worst_name = overlap, name
    base.max_overlap, base.overlaps_with = worst_overlap, worst_name

    if worst_overlap > MAX_OVERLAP:
        base.rejected_reason = (f"{worst_overlap:.0%} client overlap with existing signal "
                                f"{worst_name!r} -- not novel")
        return base

    base.passed = True
    return base


def screen(candidates: list[Candidate], frames: dict[str, pd.DataFrame],
           as_of_dates: list[date], existing: dict[str, frozenset]) -> list[ScreenResult]:
    results = []
    for candidate in candidates:
        result = screen_candidate(candidate, frames, as_of_dates, existing)
        candidate.screening = {
            "passed": result.passed, "fire_rate": result.fire_rate,
            "max_overlap": result.max_overlap, "overlaps_with": result.overlaps_with,
            "n_flagged": result.n_flagged, "rejected_reason": result.rejected_reason,
        }
        results.append(result)
    return results


def co_occurrence(flagged_by_signal: dict[str, frozenset], *,
                  min_lift: float = 1.5, min_pairs: int = 5) -> list[dict]:
    """Candidate COMBINATION rules: pairs of signals co-occurring on the
    same client more than chance would predict. Lift > 1 means the pair
    is more common than independence implies -- the same intuition behind
    config/domains_*.yaml's hand-written `combinations:` block.

    These are candidates for a human to consider, never auto-written."""
    names = sorted(flagged_by_signal)
    universe = set()
    for flagged in flagged_by_signal.values():
        universe |= flagged
    total = len(universe)
    if total == 0:
        return []

    out = []
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            a, b = flagged_by_signal[left], flagged_by_signal[right]
            both = a & b
            if len(both) < min_pairs:
                continue
            expected = (len(a) / total) * (len(b) / total) * total
            if expected <= 0:
                continue
            lift = len(both) / expected
            if lift >= min_lift:
                out.append({"when": [left, right], "co_occurring_clients": len(both),
                            "expected_by_chance": round(expected, 1), "lift": round(lift, 2)})
    return sorted(out, key=lambda r: -r["lift"])
