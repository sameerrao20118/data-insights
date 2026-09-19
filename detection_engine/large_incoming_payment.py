"""
Detector: Large Incoming Payment vs. the client's own trailing baseline.

R23 (docs/refactor_plan.md §6e): ported from the legacy pipeline's
detector of the same name into the canonical set -- same statistics
(rolling median + MAD over strictly-prior credits, an absolute floor per
currency, a cooldown), now reading canonical Transaction columns
(config/semantic_model.yaml) so it runs against ANY bound schema, and
emitting a Signal like every other detector. Spec background:
docs/detector_spec_large_incoming_payment.md. Reads only the frame it is
handed -- never labels, never protected_evaluator_only/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from detection_engine.signal import Signal, clamp_magnitude

DOMAIN = "deposits"
SIGNAL_TYPE = "large_incoming_payment"

REQUIRED_COLUMNS = ["party_id", "account_id", "posted_at", "amount", "direction"]

DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "currency", "transaction_id",
    "event_date", "detection_as_of", "flagged_amount", "baseline_median", "baseline_mad",
    "baseline_n", "threshold_multiplier", "threshold_floor", "status",
]


@dataclass(frozen=True)
class DetectorConfig:
    baseline_window_days: int
    min_baseline_transactions: int
    mad_multiplier: float
    cooldown_days: int
    rule_version: str
    absolute_floor: float = 5000.0
    absolute_floor_by_currency: dict = field(default_factory=dict)
    magnitude_saturation_at: float = 20.0  # MAD-multiples at which magnitude saturates to 1.0

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["large_incoming_payment"]
        return cls(
            baseline_window_days=d["baseline_window_days"],
            min_baseline_transactions=d["min_baseline_transactions"],
            mad_multiplier=d["mad_multiplier"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules.get("fdm_rule_version", "fdm.v1"),
            absolute_floor=d.get("absolute_floor", 5000.0),
            absolute_floor_by_currency=d.get("absolute_floor_by_currency") or {},
            magnitude_saturation_at=d.get("magnitude_saturation_at", 20.0),
        )

    def floor_for(self, currency) -> float:
        return float(self.absolute_floor_by_currency.get(str(currency).upper(), self.absolute_floor))


def _rolling_median_mad(values: np.ndarray, window_starts: np.ndarray, min_n: int):
    """median/MAD of values[window_starts[i]:i] -- strictly prior rows,
    no leakage. NaN where fewer than min_n prior rows exist."""
    n = len(values)
    med, mad, cnt = np.full(n, np.nan), np.full(n, np.nan), np.zeros(n, dtype=int)
    for i in range(n):
        window = values[window_starts[i]:i]
        cnt[i] = len(window)
        if cnt[i] >= min_n:
            m = np.median(window)
            med[i], mad[i] = m, np.median(np.abs(window - m))
    return med, mad, cnt


def detect(transactions: pd.DataFrame, config: DetectorConfig, run_id: str) -> pd.DataFrame:
    """One row per detected (or insufficient-evidence) credit. Input:
    canonical Transaction rows for one or more accounts, with party_id
    attached; `currency` and `transaction_id` optional."""
    if transactions.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)
    tx = transactions.copy()
    tx["posted_at"] = pd.to_datetime(tx["posted_at"])
    if "currency" not in tx.columns:
        tx["currency"] = "EUR"
    if "transaction_id" not in tx.columns:
        tx["transaction_id"] = tx["account_id"].astype(str) + ":" + tx["posted_at"].dt.strftime("%Y%m%d") + ":" + tx.groupby("account_id").cumcount().astype(str)
    credits = tx[tx["direction"] == "credit"]
    if credits.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    now = datetime.now(timezone.utc).isoformat()
    out_rows = []
    for (prty_id, agrmnt_id, currency), grp in credits.groupby(["party_id", "account_id", "currency"], sort=False):
        grp = grp.sort_values("posted_at").reset_index(drop=True)
        dates = grp["posted_at"].values.astype("datetime64[D]")
        amounts = grp["amount"].to_numpy(dtype=float)
        window_start = (grp["posted_at"] - pd.Timedelta(days=config.baseline_window_days)).values.astype("datetime64[D]")
        starts = np.searchsorted(dates, window_start, side="left")
        med, mad, cnt = _rolling_median_mad(amounts, starts, config.min_baseline_transactions)
        floor = config.floor_for(currency)
        for i in range(len(grp)):
            n = int(cnt[i])
            if n < config.min_baseline_transactions:
                status = "insufficient_evidence"
            else:
                threshold = med[i] + config.mad_multiplier * mad[i]
                status = "detected" if (amounts[i] > threshold and amounts[i] > floor) else "not_detected"
            if status == "not_detected":
                continue
            row = grp.iloc[i]
            out_rows.append({
                "detection_id": f"{config.rule_version}:{row['transaction_id']}",
                "rule_version": config.rule_version, "prty_id": prty_id, "agrmnt_id": agrmnt_id,
                "currency": str(currency).upper(), "transaction_id": row["transaction_id"],
                "event_date": row["posted_at"].date().isoformat(), "detection_as_of": now,
                "flagged_amount": round(float(amounts[i]), 2),
                "baseline_median": None if np.isnan(med[i]) else round(float(med[i]), 2),
                "baseline_mad": None if np.isnan(mad[i]) else round(float(mad[i]), 2),
                "baseline_n": n, "threshold_multiplier": config.mad_multiplier, "threshold_floor": floor,
                "status": status,
            })
    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    """A 'detected' row within cooldown_days of an earlier detection on
    the same account becomes 'suppressed_cooldown' -- one conversation,
    not one per payment."""
    if detections.empty:
        return detections
    out = detections.copy()
    out["_d"] = pd.to_datetime(out["event_date"])
    out = out.sort_values(["prty_id", "agrmnt_id", "_d"]).reset_index(drop=True)
    last_by_acct: dict = {}
    for i, row in out.iterrows():
        if row["status"] != "detected":
            continue
        key = (row["prty_id"], row["agrmnt_id"])
        last = last_by_acct.get(key)
        if last is not None and (row["_d"] - last).days < config.cooldown_days:
            out.at[i, "status"] = "suppressed_cooldown"
        else:
            last_by_acct[key] = row["_d"]
    return out.drop(columns=["_d"])


def to_signal(row: pd.Series) -> Signal:
    med = float(row["baseline_median"] or 0.0)
    mad = float(row["baseline_mad"] or 0.0)
    mad_multiples = (float(row["flagged_amount"]) - med) / mad if mad > 0 else 20.0
    saturation = float(row.get("magnitude_saturation_at", 20.0) or 20.0)
    return Signal(
        prty_id=row["prty_id"], signal_type=SIGNAL_TYPE, domain=DOMAIN, direction="increase",
        magnitude=clamp_magnitude(mad_multiples / saturation),
        observed_date=pd.to_datetime(row["event_date"]).date(),
        evidence_ref=f"Transaction:{row['agrmnt_id']}:{row['transaction_id']}:{row['event_date']}",
        source_tables=("Transaction",),
        raw_measure={"flagged_amount": float(row["flagged_amount"]), "baseline_median": med,
                     "baseline_n": int(row["baseline_n"]), "currency": str(row.get("currency", "EUR")).upper()},
    )
