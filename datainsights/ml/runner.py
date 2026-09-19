"""
T4 (docs/ml_strategy_plan.md §9) -- the self-service champion/challenger
runner. For every measure config/ml_policy.yaml resolves as enabled with
a real challenger algorithm, runs deterministic vs. challenger over the
whole book and writes a manifest in the SAME shape
datainsights/ml/scale_evaluation.py already uses (n_agreements,
n_disagree, runtime, data fingerprint) -- one place to read a
comparison's result regardless of which script produced it.

States plainly, every time, in the manifest and on the console:
disagreement is NOT improvement. Without real outcome labels there is no
way to say which baseline is "right" on a disagreement -- that is a
structural limitation of synthetic/unlabelled data
(docs/gap_analysis.md's SLOT E2 row), not something this runner can
resolve by running more comparisons.

Run: python -m datainsights.ml.runner --profile fdm_local [--measure BalanceObservation.balance]
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone

from datainsights.ml.compare_baselines import compare_cash_buildup, compare_revenue_pattern_change
from datainsights.ml.policy import load_policy
from datainsights.runtime import build_runtime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MANIFEST_DIR = os.path.join(REPO_ROOT, "var", "ml_runs")

# Which measure a comparison is actually wired for today. A measure
# resolving as "enabled" in policy but with no entry here is a REAL,
# disclosed gap -- reported as such in the manifest, never silently
# skipped or faked. Extending this dict is how a new measure gets a
# runnable comparison; there is no other registration point.
MEASURE_COMPARISONS = {
    "BalanceObservation.balance": ("cash_buildup", compare_cash_buildup),
    "Transaction.amount": ("revenue_pattern_change", compare_revenue_pattern_change),
}

DISAGREEMENT_CAVEAT = (
    "Disagreement counts are NOT an improvement claim. Without real "
    "outcome labels (RM engagement results), there is no way to say "
    "which baseline is right on a disagreement -- see docs/gap_analysis.md's "
    "SLOT E2 row. This manifest records WHAT changed, not whether it's better."
)


@dataclass
class MeasureRunResult:
    measure: str
    detector: str | None
    algorithm: str
    chosen_by: str
    ran: bool
    skip_reason: str | None = None
    n_agreements: int = 0
    n_disagree: int = 0
    det_detected: int = 0
    challenger_detected: int = 0
    runtime_seconds: float = 0.0


def _data_fingerprint(data_dir: str) -> str:
    parts = []
    for root, _, files in sorted(os.walk(data_dir)):
        for f in sorted(files):
            if f.endswith(".csv"):
                path = os.path.join(root, f)
                parts.append(f"{os.path.relpath(path, data_dir)}:{os.path.getsize(path)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def run_measure(measure: str, source, rules: dict, as_of: date, *,
                algorithm: str, chosen_by: str, enabled: bool = True) -> MeasureRunResult:
    """Runs ONE measure's comparison if datainsights.ml.runner.MEASURE_COMPARISONS
    has one wired -- honestly reports "not wired yet" otherwise, rather
    than silently skipping it out of the manifest entirely (a missing
    manifest entry would look identical to "nothing was configured",
    which is a different, misleading state)."""
    if not enabled:
        # The dashboard's Enabled checkbox (and a hand-edited
        # `enabled: false` in config/ml_policy.yaml) MUST actually stop a
        # comparison running. Before this check existed, a measure with
        # enabled=false but algorithm=isolation_forest still ran -- the
        # primary user-facing control was a silent no-op, which is worse
        # than not offering the control at all.
        return MeasureRunResult(measure=measure, detector=None, algorithm=algorithm,
                                chosen_by=chosen_by, ran=False,
                                skip_reason="disabled in policy -- a human turned this measure off "
                                           "(config/ml_policy.yaml, or the dashboard's Enabled checkbox)")
    if algorithm == "deterministic":
        return MeasureRunResult(measure=measure, detector=None, algorithm=algorithm,
                                chosen_by=chosen_by, ran=False,
                                skip_reason="policy resolved to the deterministic baseline -- nothing to compare")

    entry = MEASURE_COMPARISONS.get(measure)
    if entry is None:
        return MeasureRunResult(
            measure=measure, detector=None, algorithm=algorithm, chosen_by=chosen_by, ran=False,
            skip_reason=f"measure {measure!r} is enabled with a challenger but no comparison is wired "
                       f"in datainsights.ml.runner.MEASURE_COMPARISONS yet -- a real, disclosed gap, "
                       f"not a silent no-op",
        )

    detector_name, compare_fn = entry
    t0 = time.time()
    rows = compare_fn(source, rules, as_of)
    dt = time.time() - t0
    disagree = sum(1 for r in rows if not r.agrees)
    det_detected = sum(r.det_status == "detected" for r in rows)
    iso_detected = sum(r.iso_status == "detected" for r in rows)
    return MeasureRunResult(
        measure=measure, detector=detector_name, algorithm=algorithm, chosen_by=chosen_by, ran=True,
        n_agreements=len(rows), n_disagree=disagree, det_detected=det_detected,
        challenger_detected=iso_detected, runtime_seconds=round(dt, 2),
    )


def run(profile_name: str, *, measure_filter: str | None = None) -> dict:
    rt = build_runtime(profile_name)
    schema = rt.binding_name or "fdm"
    policy = load_policy(schema)
    as_of = date(2025, 10, 4)  # same fixed evaluation date compare_baselines.py/scale_evaluation.py use

    candidates = [measure_filter] if measure_filter else list(policy.measures) + [
        m for m in MEASURE_COMPARISONS if m not in policy.measures
    ]
    seen = set()
    results = []
    for measure in candidates:
        if measure in seen:
            continue
        seen.add(measure)
        resolved = policy.resolve(measure, gate1_eligible=True, mapped_by_binding=measure in MEASURE_COMPARISONS)
        result = run_measure(measure, rt.source, rt.rules, as_of,
                            algorithm=resolved.algorithm, chosen_by=resolved.chosen_by,
                            enabled=resolved.enabled)
        results.append(result)

    data_dir = rt.profile.source.data_dir
    if data_dir and not os.path.isabs(data_dir):
        data_dir = os.path.join(REPO_ROOT, data_dir)

    return {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "profile": profile_name, "schema": schema, "as_of": as_of.isoformat(),
        "data_fingerprint": _data_fingerprint(data_dir) if data_dir and os.path.isdir(data_dir) else None,
        "caveat": DISAGREEMENT_CAVEAT,
        "measures": [asdict(r) for r in results],
    }


def write_manifest(result: dict) -> str:
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    path = os.path.join(MANIFEST_DIR, f"ml_runner_{result['run_id']}.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    return path


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="fdm_local")
    parser.add_argument("--measure", default=None,
                        help="run only this measure (e.g. BalanceObservation.balance); "
                             "default runs every measure policy has an opinion on")
    args = parser.parse_args()

    print("=" * 78)
    print("Champion/challenger runner -- self-service ML comparison")
    print(DISAGREEMENT_CAVEAT)
    print("=" * 78)

    result = run(args.profile, measure_filter=args.measure)
    for m in result["measures"]:
        if not m["ran"]:
            print(f"\n{m['measure']} [{m['algorithm']}, chosen_by={m['chosen_by']}]: SKIPPED -- {m['skip_reason']}")
            continue
        print(f"\n{m['measure']} [{m['algorithm']}, chosen_by={m['chosen_by']}] "
             f"via detector {m['detector']}:")
        print(f"  {m['n_agreements']} agreements evaluated, {m['n_disagree']} disagree")
        print(f"  deterministic detected: {m['det_detected']}, challenger detected: {m['challenger_detected']}")
        print(f"  runtime: {m['runtime_seconds']}s")

    path = write_manifest(result)
    print(f"\nManifest written: {path}")


if __name__ == "__main__":
    main()
