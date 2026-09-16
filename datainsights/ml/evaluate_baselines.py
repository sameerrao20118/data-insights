"""
Challenger vs. benchmark for SLOT E2 -- does the IsolationForest baseline
actually beat the deterministic median/MAD one, and under what condition?

This answers the "effective use of ML" question with a measurement
instead of an assertion. It is a CONTROLLED INJECTION EXPERIMENT, and
must be described that way wherever its numbers are quoted:

  - Base series are real per-client credit-amount histories from the
    generated FDM dataset (read through the DataSource interface).
  - This harness injects its OWN known anomalies into copies of those
    series and checks which baseline flags them. It never reads
    protected_evaluator_only/ or any generator ground-truth label, so it
    stays on the correct side of CLAUDE.md's leakage boundary.
  - Because the anomalies are injected by the same code that scores
    them, results show RELATIVE behaviour of the two baselines under
    stated conditions -- NOT real-world detection quality.

Why a sweep and not a single scenario: an earlier single-scenario version
of this harness showed both baselines at 100% detection / 0% false
positives everywhere. That was a saturated test, not a finding -- a
median/MAD band already tolerates one outlier, and a 4x anomaly clears any
sensible threshold. A comparison that cannot fail cannot show anything.
This version sweeps the two variables the challenger's claim actually
depends on: how many past spikes contaminate the history, and how subtle
the new anomaly is.

Run: python -m datainsights.ml.evaluate_baselines
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import date, timedelta

from datainsights.ml.baselines import IsolationForestBaseline
from datainsights.ml.slots import DeterministicBaseline, InsufficientHistory

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")

MAD_MULTIPLIER = 6.0            # same k as config/rules.yaml large_incoming_payment.mad_multiplier
PRIOR_SPIKE_MULTIPLE = 25.0     # size of each contaminating past spike
CONTAMINATION_LEVELS = (0, 1, 3, 5)
INJECTED_MULTIPLES = (1.5, 2.0, 3.0)
MIN_HISTORY = 12


@dataclass
class Scorecard:
    name: str
    evaluated: int = 0
    detected: int = 0
    false_positives: int = 0
    insufficient: int = 0

    @property
    def detection_rate(self) -> float:
        return self.detected / self.evaluated if self.evaluated else 0.0

    @property
    def false_positive_rate(self) -> float:
        return self.false_positives / self.evaluated if self.evaluated else 0.0


def _is_flagged(value: float, median: float, mad: float) -> bool:
    # a zero MAD would make any deviation "infinite"; floor it at 1% of median
    tolerance = max(mad, abs(median) * 0.01)
    return value > median + MAD_MULTIPLIER * tolerance


def load_credit_histories(source, as_of: date) -> dict[str, list[tuple[date, float]]]:
    """Per deposit agreement: its credit-transaction amounts over time.
    Read through the DataSource interface -- works unchanged against a
    Snowflake-backed FDM source."""
    events = source.financial_event(as_of - timedelta(days=365 * 3), as_of)
    credits = events[events["FIN_EVNT_SBTYP_CD"] == "CRD"]
    histories: dict[str, list[tuple[date, float]]] = {}
    for agrmnt_id, grp in credits.groupby("AGRMNT_ID_TRN_ACCT"):
        grp = grp.sort_values("FIN_EVNT_PSTD_DT")
        rows = [(d if isinstance(d, date) else date.fromisoformat(str(d)[:10]), float(a))
                for d, a in zip(grp["FIN_EVNT_PSTD_DT"], grp["FIN_EVNT_AMT"])]
        if len(rows) >= MIN_HISTORY + 2:
            histories[agrmnt_id] = rows
    return histories


def run(histories: dict[str, list[tuple[date, float]]], n_contaminating: int,
        injected_multiple: float, seed: int = 7) -> tuple[Scorecard, Scorecard]:
    rng = random.Random(seed)
    det_card = Scorecard("deterministic")
    iso_card = Scorecard("challenger")

    for key, rows in histories.items():
        train = list(rows[:-2])
        _, clean_next_value = rows[-2]
        spike_date = rows[-1][0]

        train_values = sorted(v for _, v in train)
        client_median = train_values[len(train_values) // 2]

        # plant n contaminating spikes in the recent half of the history
        candidates = list(range(len(train) // 2, len(train)))
        for idx in rng.sample(candidates, min(n_contaminating, len(candidates))):
            d, _ = train[idx]
            train[idx] = (d, client_median * PRIOR_SPIKE_MULTIPLE)

        injected_value = client_median * injected_multiple
        history = {(key, "credit"): train}

        for card, model in ((det_card, DeterministicBaseline(history)),
                            (iso_card, IsolationForestBaseline(history))):
            try:
                median, mad = model.expected(key, "credit", spike_date)
            except InsufficientHistory:
                card.insufficient += 1
                continue
            card.evaluated += 1
            if _is_flagged(injected_value, median, mad):
                card.detected += 1
            if _is_flagged(clean_next_value, median, mad):
                card.false_positives += 1

    return det_card, iso_card


def sweep(histories) -> list[dict]:
    results = []
    for n in CONTAMINATION_LEVELS:
        for multiple in INJECTED_MULTIPLES:
            det, iso = run(histories, n, multiple)
            results.append({
                "contaminating_spikes": n, "injected_multiple": multiple,
                "det_detection": det.detection_rate, "iso_detection": iso.detection_rate,
                "det_false_positive": det.false_positive_rate, "iso_false_positive": iso.false_positive_rate,
                "n": det.evaluated,
            })
    return results


def verdict(results: list[dict]) -> str:
    """Applies the adoption rule mechanically, so the conclusion printed is
    derived from the numbers rather than written in advance."""
    clean = [r for r in results if r["contaminating_spikes"] == 0]
    dirty = [r for r in results if r["contaminating_spikes"] > 0]
    clean_ok = all(r["iso_detection"] >= r["det_detection"] - 0.05
                   and r["iso_false_positive"] <= r["det_false_positive"] + 0.05 for r in clean)
    dirty_gain = sum(r["iso_detection"] - r["det_detection"] for r in dirty) / len(dirty)
    dirty_fp_cost = sum(r["iso_false_positive"] - r["det_false_positive"] for r in dirty) / len(dirty)
    if not clean_ok:
        return ("KEEP DETERMINISTIC. Challenger is worse on clean history -- it would "
                "degrade the common case to help an edge case.")
    if dirty_gain > 0.05 and dirty_fp_cost <= 0.05:
        return (f"CHALLENGER EARNS A TRIAL. Matches benchmark on clean history; recovers "
                f"{dirty_gain:+.1%} detection on contaminated history at {dirty_fp_cost:+.1%} "
                f"false-positive cost. Deterministic stays the production default until "
                f"this repeats on real data on the VDI.")
    return (f"KEEP DETERMINISTIC. No material advantage on contaminated history "
            f"(detection {dirty_gain:+.1%}, false-positive {dirty_fp_cost:+.1%}). Adding a "
            f"model that doesn't measurably help is complexity without benefit.")


def main():
    from datainsights.sources.fdm_local import FdmLocalSource

    if not os.path.isdir(FDM_DIR):
        raise SystemExit("Generate FDM data first: python -m data_generator.fdm.generate_fdm")

    source = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    histories = load_credit_histories(source, date(2026, 6, 30))

    print("=" * 86)
    print("SLOT E2 -- challenger vs. benchmark (CONTROLLED INJECTION EXPERIMENT)")
    print("Anomalies injected by this harness, not real labels. Relative behaviour only,")
    print("NOT real-world detection quality.")
    print("=" * 86)
    print(f"{len(histories)} client credit histories; flag rule: value > median + "
          f"{MAD_MULTIPLIER} x MAD; each contaminating spike = {PRIOR_SPIKE_MULTIPLE}x median\n")
    print(f"{'past spikes':>11} {'anomaly':>8} | {'det detect':>10} {'ML detect':>10} | "
          f"{'det FP':>7} {'ML FP':>7}")
    print("-" * 86)
    results = sweep(histories)
    for r in results:
        print(f"{r['contaminating_spikes']:>11} {r['injected_multiple']:>7.1f}x | "
              f"{r['det_detection']:>10.1%} {r['iso_detection']:>10.1%} | "
              f"{r['det_false_positive']:>7.1%} {r['iso_false_positive']:>7.1%}")
    print("-" * 86)
    print(f"\nVerdict: {verdict(results)}")


if __name__ == "__main__":
    main()
