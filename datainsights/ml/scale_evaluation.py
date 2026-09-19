"""
Step 3 (docs/decision_record.md's own milestone plan, this session's
agentic_plan.md pairing): run the whole-book pipeline and the SLOT E2
baseline comparison at a larger synthetic scale, on an independently-
seeded holdout, and persist a versioned run manifest.

Honest architectural note before the "persist models" bit below: SLOT
E2's DeterministicBaseline and IsolationForestBaseline
(datainsights/ml/slots.py, datainsights/ml/baselines.py) are NOT trained-
once-and-saved models -- each call re-fits from that client's own history
at evaluation time, by design (never trained on the population, trivially
as-of correct, nothing to leak). There is no model.joblib artifact this
architecture produces. What genuinely needs versioning and persisting is
the RUN -- which rule_version, which baseline hyperparameters, against
which data, as of when, with what result -- so a later run can be
compared against this one. write_run_manifest() below is that, not a
fake model file forced to exist because the milestone wording assumed
one.

Run: python -m datainsights.ml.scale_evaluation
     python -m datainsights.ml.scale_evaluation --data-dir data_generator/output_fdm_scaled_holdout \
         --events-dir external_events/output_fdm_scaled_holdout --label holdout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone

import yaml

from agents.orchestrator import evaluate_book
from datainsights.correlation.dedupe import dedupe
from datainsights.fdm_worklist import build_rm_worklist
from datainsights.ml.compare_baselines import compare_cash_buildup, compare_revenue_pattern_change
from datainsights.runtime import build_runtime
from external_events.exposure_qualifier import load_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")
MANIFEST_DIR = os.path.join(REPO_ROOT, "var", "ml_runs")


def _data_fingerprint(fdm_dir: str) -> str:
    """A cheap content fingerprint (row counts per file, not a full
    hash of ~100k+ rows) -- enough to tell two runs "same data" from
    "different data" in the manifest, not a cryptographic guarantee."""
    parts = []
    for root, _, files in sorted(os.walk(fdm_dir)):
        for f in sorted(files):
            if f.endswith(".csv"):
                path = os.path.join(root, f)
                parts.append(f"{os.path.relpath(path, fdm_dir)}:{os.path.getsize(path)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def run(data_dir: str, events_dir: str, label: str) -> dict:
    events_path = os.path.join(events_dir, "tender_events.csv")
    rt = build_runtime("fdm_local", data_dir_override=data_dir, events_path_override=events_path)
    rules, source = rt.rules, rt.source
    event = load_events(events_path)[0]
    as_of = event.event_date + timedelta(days=90)

    from datainsights.semantic.binding import load_binding
    from datainsights.semantic.canonical import CanonicalSource

    parties = CanonicalSource(source, load_binding("fdm")).read("Party", as_at=as_of)
    prty_ids = sorted(parties["party_id"])

    print(f"\n=== {label}: {len(prty_ids)} clients, as_of={as_of} ===")

    t0 = time.time()
    evaluations = evaluate_book(prty_ids, source=source, rules=rules, as_of=as_of, event=event)
    t1 = time.time()
    n_signals = sum(len(e.signals) for e in evaluations)
    recommendations = dedupe([e.recommendation for e in evaluations if e.recommendation])
    worklist = build_rm_worklist(recommendations, source, rules, as_of)
    t2 = time.time()

    by_category = worklist["nba_category"].value_counts().to_dict() if not worklist.empty else {}
    total_revenue = float(worklist["indicative_revenue_eur"].dropna().sum()) if not worklist.empty else 0.0

    print(f"  whole-book pipeline: {t1 - t0:.1f}s ({len(prty_ids)} clients, "
          f"{(t1 - t0) / max(len(prty_ids), 1) * 1000:.1f}ms/client)")
    print(f"  {n_signals} signals -> {len(recommendations)} recommendations, worklist built in {t2 - t1:.1f}s")
    print(f"  by category: {by_category}")
    print(f"  indicative revenue: ~EUR {total_revenue:,.0f} (illustrative)")

    print("  baseline comparison (cash_buildup, revenue_pattern_change)...")
    t3 = time.time()
    cb_rows = compare_cash_buildup(source, rules, as_of)
    rp_rows = compare_revenue_pattern_change(source, rules, as_of)
    t4 = time.time()
    cb_disagree = sum(1 for r in cb_rows if not r.agrees)
    rp_disagree = sum(1 for r in rp_rows if not r.agrees)
    print(f"  cash_buildup: {len(cb_rows)} agreements, {cb_disagree} disagree "
          f"(det={sum(r.det_status == 'detected' for r in cb_rows)}, "
          f"iso={sum(r.iso_status == 'detected' for r in cb_rows)})")
    print(f"  revenue_pattern_change: {len(rp_rows)} agreements, {rp_disagree} disagree "
          f"(det={sum(r.det_status == 'detected' for r in rp_rows)}, "
          f"iso={sum(r.iso_status == 'detected' for r in rp_rows)})")
    print(f"  baseline comparison runtime: {t4 - t3:.1f}s")

    return {
        "label": label, "data_dir": os.path.relpath(data_dir, REPO_ROOT),
        "n_clients": len(prty_ids), "as_of": as_of.isoformat(),
        "pipeline_runtime_seconds": round(t1 - t0, 2),
        "ms_per_client": round((t1 - t0) / max(len(prty_ids), 1) * 1000, 1),
        "n_signals": n_signals, "n_recommendations": len(recommendations),
        "by_category": by_category, "total_indicative_revenue_eur": round(total_revenue, 2),
        "baseline_comparison": {
            "cash_buildup": {"n_agreements": len(cb_rows), "n_disagree": cb_disagree},
            "revenue_pattern_change": {"n_agreements": len(rp_rows), "n_disagree": rp_disagree},
            "runtime_seconds": round(t4 - t3, 2),
        },
    }


def write_run_manifest(results: list[dict], rules: dict) -> str:
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    manifest = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "rule_version": rules.get("fdm_rule_version"),
        "baseline_hyperparameters": {
            "min_observations": 8, "max_history": 30, "n_estimators": 20, "contamination": 0.1,
        },
        "results": results,
    }
    path = os.path.join(MANIFEST_DIR, f"{manifest['run_id']}.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=os.path.join(REPO_ROOT, "data_generator", "output_fdm_scaled"))
    parser.add_argument("--events-dir", default=os.path.join(REPO_ROOT, "external_events", "output_fdm_scaled"))
    parser.add_argument("--label", default="scaled_dev")
    parser.add_argument("--also-holdout", action="store_true",
                        help="also run against data_generator/output_fdm_scaled_holdout")
    args = parser.parse_args()

    if not os.path.isdir(args.data_dir):
        raise SystemExit(f"{args.data_dir} not found -- generate it first, e.g.:\n"
                         f"  python -m data_generator.fdm.generate_fdm --seed 42 --n-parties 300 "
                         f"--history-years 4 --out-dir {args.data_dir}\n"
                         f"  python -m data_generator.fdm.generate_fdm_events --fdm-dir {args.data_dir} "
                         f"--out-dir {args.events_dir}")

    with open(RULES_PATH) as f:
        rules = yaml.safe_load(f)

    print("=" * 78)
    print("Step 3 -- scaled synthetic data + baseline comparison + run manifest")
    print("Synthetic data only. Fingerprints below are file-size based, not a")
    print("cryptographic hash -- enough to distinguish runs, not to audit them.")
    print("=" * 78)

    results = [run(args.data_dir, args.events_dir, args.label)]
    results[-1]["data_fingerprint"] = _data_fingerprint(args.data_dir)

    if args.also_holdout:
        holdout_dir = os.path.join(REPO_ROOT, "data_generator", "output_fdm_scaled_holdout")
        holdout_events = os.path.join(REPO_ROOT, "external_events", "output_fdm_scaled_holdout")
        if os.path.isdir(holdout_dir):
            results.append(run(holdout_dir, holdout_events, "scaled_holdout"))
            results[-1]["data_fingerprint"] = _data_fingerprint(holdout_dir)
        else:
            print(f"\n(skipping holdout -- {holdout_dir} not generated)")

    path = write_run_manifest(results, rules)
    print(f"\nRun manifest written: {path}")


if __name__ == "__main__":
    main()
