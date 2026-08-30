"""
Detector: Large Incoming Payment vs. Client's Own Trailing Baseline.

Implements docs/detector_spec_large_incoming_payment.md. Reads ONLY the
transactions DataFrame handed to it -- never trigger_events.csv, never
anything from protected_evaluator_only/. This module must not import
anything from evaluation/.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DetectorConfig:
    baseline_window_days: int
    min_baseline_transactions: int
    mad_multiplier: float
    absolute_floor_by_currency: dict
    cooldown_days: int
    rule_version: str

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["large_incoming_payment"]
        return cls(
            baseline_window_days=d["baseline_window_days"],
            min_baseline_transactions=d["min_baseline_transactions"],
            mad_multiplier=d["mad_multiplier"],
            absolute_floor_by_currency=d["absolute_floor_by_currency"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "client_id", "account_id", "currency",
    "transaction_id", "event_date", "detection_as_of", "flagged_amount",
    "baseline_median", "baseline_mad", "baseline_n", "threshold_multiplier",
    "threshold_floor", "status",
]


def _rolling_median_mad(values: np.ndarray, window_mask_starts: np.ndarray, min_n: int):
    """For each index i, compute median and MAD of values[window_mask_starts[i]:i]
    (i.e. strictly prior rows only -- no leakage). Returns (median, mad, n)
    arrays, NaN where n < min_n."""
    n = len(values)
    med = np.full(n, np.nan)
    mad = np.full(n, np.nan)
    cnt = np.zeros(n, dtype=int)
    for i in range(n):
        lo = window_mask_starts[i]
        window = values[lo:i]
        cnt[i] = len(window)
        if cnt[i] >= min_n:
            m = np.median(window)
            med[i] = m
            mad[i] = np.median(np.abs(window - m))
    return med, mad, cnt


def detect(transactions: pd.DataFrame, config: DetectorConfig, run_id: str) -> pd.DataFrame:
    """transactions must already satisfy config/entities.yaml's transactions
    contract (validated by the DataSource layer before this is called).
    Returns a DataFrame with DETECTION_COLUMNS, one row per evaluated
    candidate transaction (status may be 'detected' or 'insufficient_evidence';
    rows below the absolute floor or non-credit are not evaluated at all)."""

    tx = transactions.copy()
    tx["booking_date"] = pd.to_datetime(tx["booking_date"])
    credits = tx[tx["direction"] == "credit"].copy()
    if credits.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    now = datetime.now(timezone.utc).isoformat()
    out_rows = []

    for (account_id, currency), grp in credits.groupby(["account_id", "currency"], sort=False):
        grp = grp.sort_values("booking_date").reset_index(drop=True)
        dates = grp["booking_date"].values.astype("datetime64[D]")
        amounts = grp["amount"].to_numpy(dtype=float)

        window_start_dt = grp["booking_date"] - pd.Timedelta(days=config.baseline_window_days)
        window_start_dt = window_start_dt.values.astype("datetime64[D]")
        # index of the first row with booking_date >= window_start for each i
        starts = np.searchsorted(dates, window_start_dt, side="left")

        med, mad, cnt = _rolling_median_mad(amounts, starts, config.min_baseline_transactions)

        floor = config.absolute_floor_by_currency.get(currency, config.absolute_floor_by_currency.get("EUR", 5000))

        for i in range(len(grp)):
            row = grp.iloc[i]
            client_id = row["client_id"]
            amount = amounts[i]
            n = int(cnt[i])
            if n < config.min_baseline_transactions:
                status = "insufficient_evidence"
                threshold_val = np.nan
            else:
                threshold_val = med[i] + config.mad_multiplier * mad[i]
                is_hit = (amount > threshold_val) and (amount > floor)
                status = "detected" if is_hit else "not_detected"

            if status == "not_detected":
                continue  # only persist detections and insufficient_evidence,
                          # not every evaluated non-event (avoid a row-per-
                          # transaction audit table growing unboundedly)

            out_rows.append({
                "detection_id": f"{config.rule_version}:{row['transaction_id']}",
                "rule_version": config.rule_version,
                "client_id": client_id,
                "account_id": account_id,
                "currency": currency,
                "transaction_id": row["transaction_id"],
                "event_date": row["booking_date"].date().isoformat(),
                "detection_as_of": now,
                "flagged_amount": round(float(amount), 2),
                "baseline_median": None if np.isnan(med[i]) else round(float(med[i]), 2),
                "baseline_mad": None if np.isnan(mad[i]) else round(float(mad[i]), 2),
                "baseline_n": n,
                "threshold_multiplier": config.mad_multiplier,
                "threshold_floor": floor,
                "status": status,
            })

    result = pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)
    return result


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    """Mark detections that fall within cooldown_days of an earlier
    'detected' row for the same (client_id, account_id) as status
    'suppressed_cooldown', keeping the earliest one active. Operates only on
    status == 'detected' rows; insufficient_evidence rows pass through."""
    if detections.empty:
        return detections
    out = detections.copy()
    out["event_date_dt"] = pd.to_datetime(out["event_date"])
    detected_mask = out["status"] == "detected"

    for (client_id, account_id), grp in out[detected_mask].groupby(["client_id", "account_id"]):
        grp_sorted = grp.sort_values("event_date_dt")
        last_active_date = None
        for idx, row in grp_sorted.iterrows():
            if last_active_date is not None and \
                    (row["event_date_dt"] - last_active_date).days < config.cooldown_days:
                out.loc[idx, "status"] = "suppressed_cooldown"
            else:
                last_active_date = row["event_date_dt"]

    return out.drop(columns=["event_date_dt"])
