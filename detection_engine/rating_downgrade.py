"""
Detector: Rating Downgrade.

docs/decision_record.md, Domain 4 (Risk): "RSK_GRD_CD migration in PARTY ->
RISK_REVIEW", and Phase 4's gate: "rating_downgrade demonstrably reads
history, not current state. That is the real test of Phase 2's bi-temporal
work." This detector exists to pass exactly that gate.

Reads PARTY's effective-dated versions (RSK_GRD_VAL, 1 = best .. 10 =
worst, per config/entities_fdm.yaml) and compares the grade valid at
`as_of` with the grade valid at `as_of - lookback_days`, each resolved
with the same as-at predicate FdmLocalSource uses. A client with no
version valid at the earlier date has no history to have been downgraded
from -- skipped, never treated as "downgraded from nothing".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

REQUIRED_COLUMNS = ["party_id", "valid_from", "valid_to", "grade_code", "grade_value"]


@dataclass(frozen=True)
class DetectorConfig:
    lookback_days: int
    min_notches: int
    cooldown_days: int
    rule_version: str

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["rating_downgrade"]
        return cls(
            lookback_days=d["lookback_days"],
            min_notches=d["min_notches"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "event_date", "detection_as_of",
    "prior_grade_cd", "prior_grade_val", "current_grade_cd", "current_grade_val",
    "notches", "grade_effective_date", "status",
]


def _version_at(versions: pd.DataFrame, when: pd.Timestamp) -> pd.Series | None:
    """The version valid at `when` -- EFFECTIVE_START_DT <= when AND
    (EFFECTIVE_END_DT is null OR EFFECTIVE_END_DT > when)."""
    end = versions["valid_to"]
    valid = versions[(versions["valid_from"] <= when) & (end.isna() | (end > when))]
    if valid.empty:
        return None
    return valid.sort_values("valid_from").iloc[-1]


def detect(party_versions: pd.DataFrame, config: DetectorConfig, run_id: str, as_of: date) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in party_versions.columns]
    if missing:
        raise ValueError(f"rating_downgrade.detect missing required columns: {missing}")
    if party_versions.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    pv = party_versions.copy()
    pv["valid_from"] = pd.to_datetime(pv["valid_from"])
    pv["valid_to"] = pd.to_datetime(pv["valid_to"].replace("", pd.NA), errors="coerce")
    now_ts, prior_ts = pd.Timestamp(as_of), pd.Timestamp(as_of - timedelta(days=config.lookback_days))
    stamp = datetime.now(timezone.utc).isoformat()

    out_rows = []
    for prty_id, versions in pv.groupby("party_id", sort=False):
        # never look past as_of: versions starting later are invisible
        versions = versions[versions["valid_from"] <= now_ts]
        current, prior = _version_at(versions, now_ts), _version_at(versions, prior_ts)
        if current is None or prior is None:
            continue
        notches = int(current["grade_value"]) - int(prior["grade_value"])
        if notches < config.min_notches:
            continue
        out_rows.append({
            "detection_id": f"{config.rule_version}:rating_downgrade:{prty_id}:{current['valid_from'].date().isoformat()}",
            "rule_version": config.rule_version,
            "prty_id": prty_id,
            "event_date": as_of.isoformat(),
            "detection_as_of": stamp,
            "prior_grade_cd": prior["grade_code"],
            "prior_grade_val": int(prior["grade_value"]),
            "current_grade_cd": current["grade_code"],
            "current_grade_val": int(current["grade_value"]),
            "notches": notches,
            "grade_effective_date": current["valid_from"].date().isoformat(),
            "status": "detected",
        })
    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    """The detection_id is keyed on the grade version's effective date, so
    the same downgrade seen on repeated runs is one detection; cooldown
    additionally suppresses a second downgrade for the same party within
    cooldown_days of the first."""
    if detections.empty:
        return detections
    out = detections.copy()
    out["_eff"] = pd.to_datetime(out["grade_effective_date"])
    for prty_id, grp in out.groupby("prty_id"):
        last_active = None
        for idx, row in grp.sort_values("_eff").iterrows():
            if last_active is not None and (row["_eff"] - last_active).days < config.cooldown_days:
                out.loc[idx, "status"] = "suppressed_cooldown"
            else:
                last_active = row["_eff"]
    return out.drop(columns=["_eff"])


def to_signal(row: pd.Series):
    from detection_engine.signal import Signal, clamp_magnitude

    return Signal(
        prty_id=row["prty_id"],
        signal_type="rating_downgrade",
        domain="risk",
        direction="decrease",  # credit quality decreased (grade value rose)
        magnitude=clamp_magnitude(int(row["notches"]) / 4),
        observed_date=date.fromisoformat(row["event_date"]),
        evidence_ref=f"PARTY:{row['prty_id']}:eff={row['grade_effective_date']}",
        source_tables=("PARTY",),
        raw_measure={"prior_grade_cd": row["prior_grade_cd"], "current_grade_cd": row["current_grade_cd"],
                     "notches": int(row["notches"])},
    )
