"""
Detector: Cash Buildup on a deposit/current account.

docs/decision_record.md, Domain 1 (Deposits): "AGRMNT_LDGR_BAL_AMT
trending up 30+ days -> TREASURY_OPPORTUNITY". Reads AGREEMENT_DAILY_BALANCE
rows for deposit-type agreements only (the caller filters to
AGRMNT_TYP_CD='DEP' and joins PRTY_ID before calling this -- this module
takes an already-prepared flat DataFrame, matching
large_incoming_payment.py's convention of never doing its own joins).

Same no-future-leakage discipline as large_incoming_payment.py: the
comparison baseline for row i is always a row strictly before it in time,
never itself or a later row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["PRTY_ID", "AGRMNT_ID", "AGRMNT_DLY_BAL_STRT_DTTM", "AGRMNT_LDGR_BAL_AMT"]


@dataclass(frozen=True)
class DetectorConfig:
    window_days: int
    min_increase_pct: float
    min_prior_balance: float
    cooldown_days: int
    rule_version: str
    # SLOT E2 (docs/decision_record.md Tab 6) -- which BaselineModel
    # computes "prior_balance": "deterministic" (default, unchanged from
    # before this option existed -- the single observation at exactly
    # window_days ago) or "isolation_forest" (datainsights.ml.baselines
    # .IsolationForestBaseline -- an outlier-robust expected value over
    # the agreement's own history, so one past spike doesn't itself
    # become next month's "normal"). Opt-in; production default stays
    # deterministic until datainsights/ml/compare_baselines.py's
    # whole-book comparison says otherwise on real data.
    baseline: str = "deterministic"

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["cash_buildup"]
        return cls(
            window_days=d["window_days"],
            min_increase_pct=d["min_increase_pct"],
            min_prior_balance=d["min_prior_balance"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
            baseline=d.get("baseline", "deterministic"),
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "event_date",
    "detection_as_of", "current_balance", "prior_balance", "increase_pct",
    "window_days", "status", "baseline",
]

VALID_BASELINES = {"deterministic", "isolation_forest"}


def detect(balances: pd.DataFrame, config: DetectorConfig, run_id: str) -> pd.DataFrame:
    """One row per agreement per evaluated day where a sustained increase
    vs. `window_days` prior is observed. `insufficient_evidence` when a
    row has no observation at least window_days in the past yet."""
    missing = [c for c in REQUIRED_COLUMNS if c not in balances.columns]
    if missing:
        raise ValueError(f"cash_buildup.detect missing required columns: {missing}")
    if config.baseline not in VALID_BASELINES:
        raise ValueError(f"cash_buildup: unknown baseline {config.baseline!r}, must be one of {VALID_BASELINES}")
    if balances.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    if config.baseline == "isolation_forest":
        from datainsights.ml.baselines import IsolationForestBaseline
        from datainsights.ml.slots import InsufficientHistory

    bal = balances.copy()
    bal["AGRMNT_DLY_BAL_STRT_DTTM"] = pd.to_datetime(bal["AGRMNT_DLY_BAL_STRT_DTTM"])
    now = datetime.now(timezone.utc).isoformat()
    out_rows = []

    for (prty_id, agrmnt_id), grp in bal.groupby(["PRTY_ID", "AGRMNT_ID"], sort=False):
        grp = grp.sort_values("AGRMNT_DLY_BAL_STRT_DTTM").reset_index(drop=True)
        dates = grp["AGRMNT_DLY_BAL_STRT_DTTM"].values.astype("datetime64[D]")
        amounts = grp["AGRMNT_LDGR_BAL_AMT"].to_numpy(dtype=float)
        window_start = grp["AGRMNT_DLY_BAL_STRT_DTTM"] - pd.Timedelta(days=config.window_days)
        window_start = window_start.values.astype("datetime64[D]")
        # last index strictly before the window_days-ago cutoff -- i.e. the
        # most recent prior observation at least window_days back
        prior_idx = np.searchsorted(dates, window_start, side="right") - 1

        if config.baseline == "isolation_forest":
            # SLOT E2 opt-in: the "prior_balance" this agreement is compared
            # against becomes an outlier-robust expected value over its OWN
            # history, instead of the single observation exactly
            # window_days ago -- see DetectorConfig.baseline's docstring.
            history = {(agrmnt_id, "balance"): [
                (d.astype("datetime64[D]").item(), float(a)) for d, a in zip(dates, amounts)
            ]}
            iso_model = IsolationForestBaseline(history)

        for i in range(len(grp)):
            if config.baseline == "isolation_forest":
                row_date = grp.iloc[i]["AGRMNT_DLY_BAL_STRT_DTTM"].date()
                try:
                    # as-at discipline: fit only on observations strictly
                    # before this row's own date, same guarantee the
                    # deterministic path's prior_idx search already gives.
                    prior_balance, _tolerance = iso_model.expected(
                        agrmnt_id, "balance", row_date - timedelta(days=1))
                except InsufficientHistory:
                    status = "insufficient_evidence"
                    prior_balance, increase_pct = None, None
                else:
                    current = amounts[i]
                    if prior_balance < config.min_prior_balance:
                        continue
                    increase_pct = (current - prior_balance) / prior_balance
                    status = "detected" if increase_pct >= config.min_increase_pct else "not_detected"
            elif prior_idx[i] < 0:
                status = "insufficient_evidence"
                prior_balance, increase_pct = None, None
            else:
                prior_balance = amounts[prior_idx[i]]
                current = amounts[i]
                if prior_balance < config.min_prior_balance:
                    continue  # too small a base to call a "buildup" meaningful
                increase_pct = (current - prior_balance) / prior_balance
                status = "detected" if increase_pct >= config.min_increase_pct else "not_detected"

            if status == "not_detected":
                continue

            row = grp.iloc[i]
            out_rows.append({
                "detection_id": f"{config.rule_version}:{agrmnt_id}:{row['AGRMNT_DLY_BAL_STRT_DTTM'].date().isoformat()}",
                "rule_version": config.rule_version,
                "prty_id": prty_id,
                "agrmnt_id": agrmnt_id,
                "event_date": row["AGRMNT_DLY_BAL_STRT_DTTM"].date().isoformat(),
                "detection_as_of": now,
                "current_balance": round(float(amounts[i]), 2),
                "prior_balance": None if prior_balance is None else round(float(prior_balance), 2),
                "increase_pct": None if increase_pct is None else round(float(increase_pct), 4),
                "window_days": config.window_days,
                "baseline": config.baseline,
                "status": status,
            })

    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    if detections.empty:
        return detections
    out = detections.copy()
    out["event_date_dt"] = pd.to_datetime(out["event_date"])
    detected_mask = out["status"] == "detected"

    for (prty_id, agrmnt_id), grp in out[detected_mask].groupby(["prty_id", "agrmnt_id"]):
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

    magnitude = clamp_magnitude((row["increase_pct"] or 0) / 0.5)  # 50%+ saturates
    return Signal(
        prty_id=row["prty_id"],
        signal_type="cash_buildup",
        domain="deposits",
        direction="increase",
        magnitude=magnitude,
        observed_date=date_cls.fromisoformat(row["event_date"]),
        evidence_ref=f"AGREEMENT_DAILY_BALANCE:{row['agrmnt_id']}:eff={row['event_date']}",
        source_tables=("AGREEMENT_DAILY_BALANCE",),
        raw_measure={"current_balance": row["current_balance"], "prior_balance": row["prior_balance"],
                     "increase_pct": row["increase_pct"], "baseline": row.get("baseline", "deterministic")},
    )
