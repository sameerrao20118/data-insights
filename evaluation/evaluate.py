"""
Evaluation interface, deliberately separate from detection_engine/. Reads
detections (from the state store) plus protected ground truth
(trigger_events.csv) -- the detector itself never sees this file; see
data_generator/output/protected_evaluator_only/README.md.

*** ALL NUMBERS THIS MODULE PRODUCES ARE DEVELOPMENT DIAGNOSTICS, NOT A
CLEAN HOLDOUT BENCHMARK. *** The coding session that built
detection_engine/large_incoming_payment.py also authored the trigger-
injection logic in data_generator/generate_data.py and has directly
inspected trigger_events.csv. Any precision/recall here reflects a
contaminated development loop -- useful for catching gross bugs, not for
claiming detection quality. See docs/detector_spec_large_incoming_payment.md.

Matching policy (frozen here, before running -- project instructions
section 6): a detection matches a ground-truth event if client_id matches
AND |detection.event_date - ground_truth.event_date| <= match_window_days.
One-to-one: each ground-truth event can be matched by at most one
detection (earliest by date if multiple candidates), and vice versa, via
greedy nearest-date assignment. Ground-truth trigger types other than
TENDER_PAYMENT are out of scope for this detector and excluded from the
denominator with that noted explicitly -- this detector was never intended
to catch e.g. CASHFLOW_STRESS or FX_EXPOSURE_NEW.
"""

from __future__ import annotations

import sqlite3

import pandas as pd
import yaml

# Only trigger types this detector could plausibly catch are counted in the
# denominator. Injected trigger-type names are used here only because this
# evaluation module is explicitly the evaluator role, not the detector --
# the detector code never sees these names or this file.
IN_SCOPE_TRIGGER_TYPES = ["TENDER_PAYMENT"]


def load_detections_from_state(state_path: str) -> pd.DataFrame:
    con = sqlite3.connect(state_path)
    df = pd.read_sql_query(
        "SELECT * FROM detections WHERE status = 'detected'", con
    )
    con.close()
    return df


def load_ground_truth(trigger_events_path: str) -> pd.DataFrame:
    df = pd.read_csv(trigger_events_path)
    return df[df["trigger_type"].isin(IN_SCOPE_TRIGGER_TYPES)].copy()


def match(detections: pd.DataFrame, ground_truth: pd.DataFrame, match_window_days: int) -> dict:
    det = detections.copy()
    det["event_date"] = pd.to_datetime(det["event_date"])
    gt = ground_truth.copy()
    gt["event_date"] = pd.to_datetime(gt["event_date"])

    gt_matched = set()
    det_matched = set()
    pairs = []

    for client_id, gt_grp in gt.groupby("client_id"):
        det_grp = det[det["client_id"] == client_id]
        for gt_idx, gt_row in gt_grp.iterrows():
            candidates = det_grp[
                (~det_grp.index.isin(det_matched))
                & ((det_grp["event_date"] - gt_row["event_date"]).abs().dt.days <= match_window_days)
            ]
            if candidates.empty:
                continue
            best = candidates.assign(
                delta=(candidates["event_date"] - gt_row["event_date"]).abs()
            ).sort_values("delta").iloc[0]
            det_matched.add(best.name)
            gt_matched.add(gt_idx)
            pairs.append((gt_idx, best.name))

    tp = len(pairs)
    fn = len(gt) - len(gt_matched)
    fp = len(det) - len(det_matched)
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall and precision + recall > 0) else float("nan")

    return {
        "tp": tp, "fp": fp, "fn": fn,
        "n_ground_truth_in_scope": len(gt), "n_detections": len(det),
        "precision": precision, "recall": recall, "f1": f1,
        "match_window_days": match_window_days,
    }


def report(dataset_label: str, detections_path: str, trigger_events_path: str,
           match_window_days: int) -> dict:
    detections = load_detections_from_state(detections_path)
    gt = load_ground_truth(trigger_events_path)
    result = match(detections, gt, match_window_days)
    print(f"\n=== {dataset_label} ===")
    print("*** DEVELOPMENT DIAGNOSTIC -- CONTAMINATED SESSION, NOT A CLEAN BENCHMARK ***")
    print(f"In-scope ground truth events (TENDER_PAYMENT only): {result['n_ground_truth_in_scope']}")
    print(f"Active detections evaluated: {result['n_detections']}")
    print(f"TP={result['tp']}  FP={result['fp']}  FN={result['fn']}")
    print(f"Precision={result['precision']:.2f}  Recall={result['recall']:.2f}  F1={result['f1']:.2f}")
    print(f"(match window: +/-{match_window_days} days, one-to-one greedy nearest-date matching)")
    return result


if __name__ == "__main__":
    with open("config/rules.yaml") as f:
        rules = yaml.safe_load(f)
    window = rules["evaluation"]["match_window_days"]
    report(
        "dev dataset (data_generator/output) -- SAME session authored the injections",
        "var/state.sqlite",
        "data_generator/output/protected_evaluator_only/trigger_events.csv",
        window,
    )
