"""
Detector: Dormancy on a deposit/current account.

docs/decision_record.md, Domain 1 (Deposits): "Zero EVENT_FINANCIAL for
90+ days -> ADVISORY_ONLY". Unlike large_incoming_payment/cash_buildup,
this is a point-in-time snapshot question ("is this account currently
dormant") rather than a per-row historical crossing, so detect() takes an
explicit `as_of` date rather than evaluating every row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

REQUIRED_COLUMNS = ["PRTY_ID", "AGRMNT_ID", "FIN_EVNT_PSTD_DT"]


@dataclass(frozen=True)
class DetectorConfig:
    dormancy_days: int
    cooldown_days: int
    rule_version: str

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["dormancy"]
        return cls(
            dormancy_days=d["dormancy_days"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "event_date",
    "detection_as_of", "last_event_date", "days_since_last_event", "status",
]


def detect(events: pd.DataFrame, agreement_ids: list, config: DetectorConfig,
           run_id: str, as_of: date) -> pd.DataFrame:
    """`agreement_ids` is the full set of deposit agreements in scope
    (including ones with zero events ever, e.g. a newly opened account --
    those are 'insufficient_evidence', not 'detected', since there's no
    prior activity to have gone dormant from)."""
    missing = [c for c in REQUIRED_COLUMNS if c not in events.columns] if not events.empty else []
    if missing:
        raise ValueError(f"dormancy.detect missing required columns: {missing}")

    now = datetime.now(timezone.utc).isoformat()
    out_rows = []

    if events.empty:
        last_by_agrmnt = pd.Series(dtype="datetime64[ns]")
        prty_by_agrmnt = {}
    else:
        ev = events.copy()
        ev["FIN_EVNT_PSTD_DT"] = pd.to_datetime(ev["FIN_EVNT_PSTD_DT"])
        last_by_agrmnt = ev.groupby("AGRMNT_ID")["FIN_EVNT_PSTD_DT"].max()
        prty_by_agrmnt = ev.drop_duplicates("AGRMNT_ID").set_index("AGRMNT_ID")["PRTY_ID"].to_dict()

    for agrmnt_id in agreement_ids:
        if agrmnt_id not in last_by_agrmnt.index:
            continue  # never had any activity -- not evaluable as "gone dormant"
        last_date = last_by_agrmnt.loc[agrmnt_id]
        days_since = (pd.Timestamp(as_of) - last_date).days
        if days_since < config.dormancy_days:
            continue
        out_rows.append({
            "detection_id": f"{config.rule_version}:{agrmnt_id}",
            "rule_version": config.rule_version,
            "prty_id": prty_by_agrmnt[agrmnt_id],
            "agrmnt_id": agrmnt_id,
            "event_date": as_of.isoformat(),
            "detection_as_of": now,
            "last_event_date": last_date.date().isoformat(),
            "days_since_last_event": int(days_since),
            "status": "detected",
        })

    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    """A dormant account stays dormant across reruns until it's active
    again -- cooldown here means 'don't re-alert on the same still-dormant
    account within cooldown_days of the first time it was flagged', keyed
    by agreement, using detection_as_of as the run clock."""
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

    magnitude = clamp_magnitude(row["days_since_last_event"] / 365)
    return Signal(
        prty_id=row["prty_id"],
        signal_type="dormancy",
        domain="deposits",
        direction="decrease",
        magnitude=magnitude,
        observed_date=date.fromisoformat(row["event_date"]),
        evidence_ref=f"EVENT_FINANCIAL:{row['agrmnt_id']}:last={row['last_event_date']}",
        source_tables=("EVENT_FINANCIAL",),
        raw_measure={"days_since_last_event": row["days_since_last_event"]},
    )
