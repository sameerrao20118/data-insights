"""
Detector: Facility Maturity Approaching.

docs/decision_record.md, Domain 2 (Lending): "AGRMNT_CLOSE_DT < 90 days ->
FINANCING_NEED (renewal)". Point-in-time snapshot -- takes an explicit
as_of date, same shape as dormancy.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

REQUIRED_COLUMNS = ["party_id", "account_id", "close_date"]


@dataclass(frozen=True)
class DetectorConfig:
    horizon_days: int
    cooldown_days: int
    rule_version: str

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["facility_maturity_approaching"]
        return cls(
            horizon_days=d["horizon_days"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "event_date",
    "detection_as_of", "close_date", "days_to_close", "orig_limit", "status",
]


def detect(agreements: pd.DataFrame, config: DetectorConfig, run_id: str, as_of: date) -> pd.DataFrame:
    """Agreements with a null/blank AGRMNT_CLOSE_DT (e.g. a deposit
    account) are excluded, not insufficient_evidence -- 'no scheduled
    close date' is a different fact from 'not enough evidence to know'."""
    missing = [c for c in REQUIRED_COLUMNS if c not in agreements.columns]
    if missing:
        raise ValueError(f"facility_maturity_approaching.detect missing required columns: {missing}")

    agr = agreements.dropna(subset=["close_date"]).copy()
    agr = agr[agr["close_date"] != ""]
    if agr.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    agr["close_date"] = pd.to_datetime(agr["close_date"])
    agr["days_to_close"] = (agr["close_date"] - pd.Timestamp(as_of)).dt.days
    now = datetime.now(timezone.utc).isoformat()

    hits = agr[(agr["days_to_close"] >= 0) & (agr["days_to_close"] <= config.horizon_days)]
    out_rows = []
    for _, row in hits.iterrows():
        out_rows.append({
            "detection_id": f"{config.rule_version}:{row['account_id']}",
            "rule_version": config.rule_version,
            "prty_id": row["party_id"],
            "agrmnt_id": row["account_id"],
            "event_date": as_of.isoformat(),
            "detection_as_of": now,
            "close_date": row["close_date"].date().isoformat(),
            "days_to_close": int(row["days_to_close"]),
            # optional -- carried so a renewal can be sized at the current
            # limit; None when the caller's frame has no limit column
            "orig_limit": (float(row["original_limit"])
                           if "original_limit" in row.index and pd.notna(row["original_limit"])
                           else None),
            "status": "detected",
        })
    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    if detections.empty:
        return detections
    out = detections.copy()
    out["detection_as_of_dt"] = pd.to_datetime(out["detection_as_of"])
    for agrmnt_id, grp in out.groupby("agrmnt_id"):
        first_seen = grp["detection_as_of_dt"].min()
        for idx, row in grp.iterrows():
            if row["detection_as_of_dt"] > first_seen and \
                    (row["detection_as_of_dt"] - first_seen).days < config.cooldown_days:
                out.loc[idx, "status"] = "suppressed_cooldown"
    return out.drop(columns=["detection_as_of_dt"])


def to_signal(row: pd.Series):
    from detection_engine.signal import Signal, clamp_magnitude

    magnitude = clamp_magnitude(1.0 - (row["days_to_close"] / 90))
    return Signal(
        prty_id=row["prty_id"],
        signal_type="facility_maturity_approaching",
        domain="lending",
        direction="neutral",
        magnitude=magnitude,
        observed_date=date.fromisoformat(row["event_date"]),
        evidence_ref=f"AGREEMENT:{row['agrmnt_id']}:close={row['close_date']}",
        source_tables=("AGREEMENT",),
        raw_measure={"close_date": row["close_date"], "days_to_close": row["days_to_close"],
                     "orig_limit": row.get("orig_limit")},
    )
