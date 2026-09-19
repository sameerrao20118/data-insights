"""
Detector: Fixed Rate Expiry on a mortgage-type agreement.

docs/decision_record.md, Domain 2 (Lending): "MORT_FXED_RT_END_DT
approaching -> TREASURY_OPPORTUNITY or HEDGING_NEED" (direction depends on
rate context, which this detector does not itself resolve -- it only
reports the approaching-expiry fact; category/direction resolution is a
correlation-layer concern, per docs/decision_record.md's own ambiguity on
this signal). Point-in-time snapshot, same shape as
facility_maturity_approaching.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

REQUIRED_COLUMNS = ["party_id", "account_id", "fixed_rate_end_date"]


@dataclass(frozen=True)
class DetectorConfig:
    horizon_days: int
    cooldown_days: int
    rule_version: str

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["fixed_rate_expiry"]
        return cls(
            horizon_days=d["horizon_days"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "event_date",
    "detection_as_of", "fixed_rate_end_date", "days_to_expiry", "status",
]


def detect(mortgages: pd.DataFrame, config: DetectorConfig, run_id: str, as_of: date) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in mortgages.columns]
    if missing:
        raise ValueError(f"fixed_rate_expiry.detect missing required columns: {missing}")

    m = mortgages.dropna(subset=["fixed_rate_end_date"]).copy()
    m = m[m["fixed_rate_end_date"] != ""]
    if m.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    m["fixed_rate_end_date"] = pd.to_datetime(m["fixed_rate_end_date"])
    m["days_to_expiry"] = (m["fixed_rate_end_date"] - pd.Timestamp(as_of)).dt.days
    now = datetime.now(timezone.utc).isoformat()

    hits = m[(m["days_to_expiry"] >= 0) & (m["days_to_expiry"] <= config.horizon_days)]
    out_rows = []
    for _, row in hits.iterrows():
        out_rows.append({
            "detection_id": f"{config.rule_version}:{row['account_id']}",
            "rule_version": config.rule_version,
            "prty_id": row["party_id"],
            "agrmnt_id": row["account_id"],
            "event_date": as_of.isoformat(),
            "detection_as_of": now,
            "fixed_rate_end_date": row["fixed_rate_end_date"].date().isoformat(),
            "days_to_expiry": int(row["days_to_expiry"]),
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

    magnitude = clamp_magnitude(1.0 - (row["days_to_expiry"] / 90))
    return Signal(
        prty_id=row["prty_id"],
        signal_type="fixed_rate_expiry",
        domain="lending",
        direction="neutral",
        magnitude=magnitude,
        observed_date=date.fromisoformat(row["event_date"]),
        evidence_ref=f"MORTGAGE_AGREEMENT:{row['agrmnt_id']}:expiry={row['fixed_rate_end_date']}",
        source_tables=("MORTGAGE_AGREEMENT",),
        raw_measure={"fixed_rate_end_date": row["fixed_rate_end_date"], "days_to_expiry": row["days_to_expiry"]},
    )
