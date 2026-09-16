"""
Detector: Facility Utilization Spike.

docs/decision_record.md, Domain 2 (Lending): "Daily drawdown > 85% of
AGRMNT_ORIG_LIM -> FINANCING_NEED". Reads AGREEMENT_DAILY_BALANCE for
credit-facility-type agreements joined with AGRMNT_ORIG_LIM (caller
filters to AGRMNT_TYP_CD in {LON, ODR} and joins PRTY_ID/AGRMNT_ORIG_LIM
before calling this).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

REQUIRED_COLUMNS = ["PRTY_ID", "AGRMNT_ID", "AGRMNT_DLY_BAL_STRT_DTTM",
                    "AGRMNT_LDGR_BAL_AMT", "AGRMNT_ORIG_LIM"]


@dataclass(frozen=True)
class DetectorConfig:
    utilization_threshold_pct: float
    cooldown_days: int
    rule_version: str

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["facility_utilization_spike"]
        return cls(
            utilization_threshold_pct=d["utilization_threshold_pct"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "event_date",
    "detection_as_of", "drawn_amount", "orig_limit", "utilization_pct", "status",
]


def detect(balances: pd.DataFrame, config: DetectorConfig, run_id: str) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in balances.columns]
    if missing:
        raise ValueError(f"facility_utilization_spike.detect missing required columns: {missing}")
    if balances.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    bal = balances.copy()
    bal = bal[bal["AGRMNT_ORIG_LIM"] > 0]  # a DEP row slipping in has no limit -- not evaluable
    if bal.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)
    bal["utilization_pct"] = bal["AGRMNT_LDGR_BAL_AMT"] / bal["AGRMNT_ORIG_LIM"]
    now = datetime.now(timezone.utc).isoformat()

    hits = bal[bal["utilization_pct"] > config.utilization_threshold_pct]
    out_rows = []
    for _, row in hits.iterrows():
        event_date = pd.to_datetime(row["AGRMNT_DLY_BAL_STRT_DTTM"]).date().isoformat()
        out_rows.append({
            "detection_id": f"{config.rule_version}:{row['AGRMNT_ID']}:{event_date}",
            "rule_version": config.rule_version,
            "prty_id": row["PRTY_ID"],
            "agrmnt_id": row["AGRMNT_ID"],
            "event_date": event_date,
            "detection_as_of": now,
            "drawn_amount": round(float(row["AGRMNT_LDGR_BAL_AMT"]), 2),
            "orig_limit": round(float(row["AGRMNT_ORIG_LIM"]), 2),
            "utilization_pct": round(float(row["utilization_pct"]), 4),
            "status": "detected",
        })
    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    if detections.empty:
        return detections
    out = detections.copy()
    out["event_date_dt"] = pd.to_datetime(out["event_date"])
    for agrmnt_id, grp in out.groupby("agrmnt_id"):
        last_active = None
        for idx, row in grp.sort_values("event_date_dt").iterrows():
            if last_active is not None and (row["event_date_dt"] - last_active).days < config.cooldown_days:
                out.loc[idx, "status"] = "suppressed_cooldown"
            else:
                last_active = row["event_date_dt"]
    return out.drop(columns=["event_date_dt"])


def to_signal(row: pd.Series):
    from datetime import date as date_cls
    from detection_engine.signal import Signal, clamp_magnitude

    magnitude = clamp_magnitude((row["utilization_pct"] - 0.85) / 0.15)
    return Signal(
        prty_id=row["prty_id"],
        signal_type="facility_utilization_spike",
        domain="lending",
        direction="increase",
        magnitude=magnitude,
        observed_date=date_cls.fromisoformat(row["event_date"]),
        evidence_ref=f"AGREEMENT_DAILY_BALANCE:{row['agrmnt_id']}:eff={row['event_date']}",
        source_tables=("AGREEMENT_DAILY_BALANCE", "AGREEMENT"),
        raw_measure={"drawn_amount": row["drawn_amount"], "orig_limit": row["orig_limit"],
                     "utilization_pct": row["utilization_pct"]},
    )
