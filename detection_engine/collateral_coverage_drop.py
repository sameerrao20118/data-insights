"""
Detector: Collateral Coverage Drop.

docs/decision_record.md, Domain 2 (Lending): "COLLATERAL_ITEM_VALUE vs
exposure -> RISK_REVIEW". This pass's substitute proof entity for
bi-temporal as-at correctness (Risk domain's rating_downgrade is out of
scope -- see config/entities_fdm.yaml's note on COLLATERAL_ITEM_VALUE).

Takes an as_at date and reads coverage history (already joined by the
caller: PRTY_ID, AGRMNT_ID, CLTRL_ITEM_ID, AGRMNT_ORIG_LIM,
CLTRL_VAL_AMT, EFFECTIVE_START_DT across however many valuation versions
exist) -- flags a facility where CURRENT coverage is below threshold AND
was above threshold in an earlier version, i.e. an actual drop, not
permanently-thin collateral.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

REQUIRED_COLUMNS = ["PRTY_ID", "AGRMNT_ID", "CLTRL_ITEM_ID", "AGRMNT_ORIG_LIM",
                    "CLTRL_VAL_AMT", "EFFECTIVE_START_DT"]


@dataclass(frozen=True)
class DetectorConfig:
    coverage_threshold_pct: float
    cooldown_days: int
    rule_version: str

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["collateral_coverage_drop"]
        return cls(
            coverage_threshold_pct=d["coverage_threshold_pct"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "cltrl_item_id",
    "event_date", "detection_as_of", "current_coverage_pct",
    "prior_max_coverage_pct", "status",
]


def detect(coverage_history: pd.DataFrame, config: DetectorConfig, run_id: str, as_at: date) -> pd.DataFrame:
    """`coverage_history` carries every valuation version up to and
    including `as_at` for each (AGRMNT_ID, CLTRL_ITEM_ID) -- the caller
    is responsible for the as-at filter (via FdmLocalSource.as_at for the
    current version, plus the full history for the prior-max check).
    Flags only where coverage was previously *above* threshold and has
    since fallen below it -- that's the "drop", not a static thin-coverage
    state that would fire every single run."""
    missing = [c for c in REQUIRED_COLUMNS if c not in coverage_history.columns]
    if missing:
        raise ValueError(f"collateral_coverage_drop.detect missing required columns: {missing}")
    if coverage_history.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    ch = coverage_history.copy()
    ch["EFFECTIVE_START_DT"] = pd.to_datetime(ch["EFFECTIVE_START_DT"])
    ch = ch[ch["EFFECTIVE_START_DT"] <= pd.Timestamp(as_at)]
    ch = ch[ch["AGRMNT_ORIG_LIM"] > 0]
    if ch.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)
    ch["coverage_pct"] = ch["CLTRL_VAL_AMT"] / ch["AGRMNT_ORIG_LIM"]

    now = datetime.now(timezone.utc).isoformat()
    out_rows = []

    for (agrmnt_id, cltrl_item_id), grp in ch.groupby(["AGRMNT_ID", "CLTRL_ITEM_ID"], sort=False):
        grp = grp.sort_values("EFFECTIVE_START_DT")
        if len(grp) < 2:
            continue  # no prior version to compare against -- can't tell "drop" from "always thin"
        current = grp.iloc[-1]
        prior_max = grp.iloc[:-1]["coverage_pct"].max()

        if current["coverage_pct"] >= config.coverage_threshold_pct:
            continue
        if prior_max < config.coverage_threshold_pct:
            continue  # never was adequately covered -- not a "drop"

        out_rows.append({
            "detection_id": f"{config.rule_version}:{agrmnt_id}:{cltrl_item_id}",
            "rule_version": config.rule_version,
            "prty_id": current["PRTY_ID"],
            "agrmnt_id": agrmnt_id,
            "cltrl_item_id": cltrl_item_id,
            "event_date": as_at.isoformat(),
            "detection_as_of": now,
            "current_coverage_pct": round(float(current["coverage_pct"]), 4),
            "prior_max_coverage_pct": round(float(prior_max), 4),
            "status": "detected",
        })

    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    if detections.empty:
        return detections
    out = detections.copy()
    out["detection_as_of_dt"] = pd.to_datetime(out["detection_as_of"])
    for key, grp in out.groupby(["agrmnt_id", "cltrl_item_id"]):
        first_seen = grp["detection_as_of_dt"].min()
        for idx, row in grp.iterrows():
            if row["detection_as_of_dt"] > first_seen and \
                    (row["detection_as_of_dt"] - first_seen).days < config.cooldown_days:
                out.loc[idx, "status"] = "suppressed_cooldown"
    return out.drop(columns=["detection_as_of_dt"])


def to_signal(row: pd.Series):
    from detection_engine.signal import Signal, clamp_magnitude

    drop = row["prior_max_coverage_pct"] - row["current_coverage_pct"]
    magnitude = clamp_magnitude(drop / 0.5)
    return Signal(
        prty_id=row["prty_id"],
        signal_type="collateral_coverage_drop",
        domain="lending",
        direction="decrease",
        magnitude=magnitude,
        observed_date=date.fromisoformat(row["event_date"]),
        evidence_ref=f"COLLATERAL_ITEM_VALUE:{row['cltrl_item_id']}:eff={row['event_date']}",
        source_tables=("COLLATERAL_ITEM_VALUE", "AGREEMENT_COLLATERAL_ITEM", "AGREEMENT"),
        raw_measure={"current_coverage_pct": row["current_coverage_pct"],
                     "prior_max_coverage_pct": row["prior_max_coverage_pct"]},
    )
