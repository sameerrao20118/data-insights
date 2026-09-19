"""
Detector: PD Migration.

docs/decision_record.md, Domain 4 (Risk): "Model output shifts in
LC/ML_MODEL_DATA -> early FINANCING_NEED or RISK_REVIEW", with the Phase 4
warning: "Encode risk-grade scale direction explicitly -- a PD improving is
a growth signal, easy to invert."

Reads an effective-dated PD history (PARTY_METRIC rows with
PRTY_MTR_TYP_CD = 'PD_1Y') and compares the PD valid at `as_of` with the
PD valid at `as_of - lookback_days`.

Direction is encoded explicitly, never inferred downstream:
  - PD UP   (deterioration) -> direction "increase" -- a risk signal.
  - PD DOWN (improvement)   -> direction "decrease" -- a growth signal.
Improvements are NOT emitted by default (`emit_improvements: false`),
because the correlation layer currently maps signal_type -> category
without looking at direction. An improvement flowing through as
RISK_REVIEW would wrongly suppress a client's revenue recommendations --
exactly the inversion the decision record warns about. Turn improvements
on only once the category mapping is direction-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

REQUIRED_COLUMNS = ["party_id", "valid_from", "valid_to", "metric_type", "value"]


@dataclass(frozen=True)
class DetectorConfig:
    lookback_days: int
    min_relative_change: float
    min_abs_change: float
    emit_improvements: bool
    cooldown_days: int
    rule_version: str
    metric_type: str = "PD_1Y"

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["pd_migration"]
        return cls(
            lookback_days=d["lookback_days"],
            min_relative_change=d["min_relative_change"],
            min_abs_change=d["min_abs_change"],
            emit_improvements=d.get("emit_improvements", False),
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "event_date", "detection_as_of",
    "prior_pd_pct", "current_pd_pct", "change_pct", "direction", "pd_effective_date", "status",
]


def _value_at(series: pd.DataFrame, when: pd.Timestamp) -> pd.Series | None:
    end = series["valid_to"]
    valid = series[(series["valid_from"] <= when) & (end.isna() | (end > when))]
    if valid.empty:
        return None
    return valid.sort_values("valid_from").iloc[-1]


def detect(metrics: pd.DataFrame, config: DetectorConfig, run_id: str, as_of: date) -> pd.DataFrame:
    missing = [c for c in REQUIRED_COLUMNS if c not in metrics.columns]
    if missing:
        raise ValueError(f"pd_migration.detect missing required columns: {missing}")
    pd_rows = metrics[metrics["metric_type"] == config.metric_type].copy()
    if pd_rows.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    pd_rows["valid_from"] = pd.to_datetime(pd_rows["valid_from"])
    pd_rows["valid_to"] = pd.to_datetime(pd_rows["valid_to"].replace("", pd.NA), errors="coerce")
    now_ts, prior_ts = pd.Timestamp(as_of), pd.Timestamp(as_of - timedelta(days=config.lookback_days))
    stamp = datetime.now(timezone.utc).isoformat()

    out_rows = []
    for prty_id, series in pd_rows.groupby("party_id", sort=False):
        series = series[series["valid_from"] <= now_ts]  # no future versions
        current, prior = _value_at(series, now_ts), _value_at(series, prior_ts)
        if current is None or prior is None:
            continue
        cur_pd, prior_pd = float(current["value"]), float(prior["value"])
        if prior_pd <= 0:
            continue  # relative change undefined -- not evaluable, not "infinite migration"
        abs_change = cur_pd - prior_pd
        rel_change = abs_change / prior_pd
        if abs(abs_change) < config.min_abs_change or abs(rel_change) < config.min_relative_change:
            continue
        direction = "increase" if abs_change > 0 else "decrease"
        if direction == "decrease" and not config.emit_improvements:
            continue
        out_rows.append({
            "detection_id": f"{config.rule_version}:pd_migration:{prty_id}:{current['valid_from'].date().isoformat()}",
            "rule_version": config.rule_version,
            "prty_id": prty_id,
            "event_date": as_of.isoformat(),
            "detection_as_of": stamp,
            "prior_pd_pct": round(prior_pd, 6),
            "current_pd_pct": round(cur_pd, 6),
            "change_pct": round(rel_change, 4),
            "direction": direction,
            "pd_effective_date": current["valid_from"].date().isoformat(),
            "status": "detected",
        })
    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    if detections.empty:
        return detections
    out = detections.copy()
    out["_eff"] = pd.to_datetime(out["pd_effective_date"])
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
        signal_type="pd_migration",
        domain="risk",
        direction=row["direction"],
        magnitude=clamp_magnitude(abs(float(row["change_pct"])) / 2),
        observed_date=date.fromisoformat(row["event_date"]),
        evidence_ref=f"PARTY_METRIC:{row['prty_id']}:PD_1Y:eff={row['pd_effective_date']}",
        source_tables=("PARTY_METRIC",),
        raw_measure={"prior_pd_pct": float(row["prior_pd_pct"]), "current_pd_pct": float(row["current_pd_pct"]),
                     "change_pct": float(row["change_pct"])},
    )
