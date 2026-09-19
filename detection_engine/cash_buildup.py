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

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["party_id", "account_id", "observed_at", "balance"]


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

    # R17: per-currency floors (config/rules.yaml cash_buildup.min_prior_balance_by_currency);
    # a currency not listed falls back to min_prior_balance.
    min_prior_balance_by_currency: dict = field(default_factory=dict)

    def floor_for(self, currency) -> float:
        return float(self.min_prior_balance_by_currency.get(str(currency).upper(), self.min_prior_balance))

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["cash_buildup"]
        return cls(
            window_days=d["window_days"],
            min_increase_pct=d["min_increase_pct"],
            min_prior_balance=d["min_prior_balance"],
            cooldown_days=d["cooldown_days"],
            min_prior_balance_by_currency=d.get("min_prior_balance_by_currency") or {},
            rule_version=rules["fdm_rule_version"],
            baseline=d.get("baseline", "deterministic"),
        )


DETECTION_COLUMNS = [
    "currency",  # R17: which currency the floor and the amounts are in
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
    bal["observed_at"] = pd.to_datetime(bal["observed_at"])
    now = datetime.now(timezone.utc).isoformat()
    out_rows = []

    for (prty_id, agrmnt_id), grp in bal.groupby(["party_id", "account_id"], sort=False):
        # R17: the floor is per currency when the frame carries one
        currency = str(grp["currency"].iloc[0]).upper() if "currency" in grp.columns else "EUR"
        floor = config.floor_for(currency)
        grp = grp.sort_values("observed_at").reset_index(drop=True)
        dates = grp["observed_at"].values.astype("datetime64[D]")
        amounts = grp["balance"].to_numpy(dtype=float)
        window_start = grp["observed_at"] - pd.Timedelta(days=config.window_days)
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
                row_date = grp.iloc[i]["observed_at"].date()
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
                    if prior_balance < floor:
                        continue
                    increase_pct = (current - prior_balance) / prior_balance
                    status = "detected" if increase_pct >= config.min_increase_pct else "not_detected"
            elif prior_idx[i] < 0:
                status = "insufficient_evidence"
                prior_balance, increase_pct = None, None
            else:
                prior_balance = amounts[prior_idx[i]]
                current = amounts[i]
                if prior_balance < floor:
                    continue  # too small a base to call a "buildup" meaningful
                increase_pct = (current - prior_balance) / prior_balance
                status = "detected" if increase_pct >= config.min_increase_pct else "not_detected"

            if status == "not_detected":
                continue

            row = grp.iloc[i]
            out_rows.append({
                "detection_id": f"{config.rule_version}:{agrmnt_id}:{row['observed_at'].date().isoformat()}",
                "rule_version": config.rule_version,
                "prty_id": prty_id,
                "currency": currency,
                "agrmnt_id": agrmnt_id,
                "event_date": row["observed_at"].date().isoformat(),
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
