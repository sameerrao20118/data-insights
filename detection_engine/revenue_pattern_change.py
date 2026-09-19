"""
Detector: Structural Revenue Pattern Change on a deposit/current account.

docs/decision_record.md, Domain 1 (Deposits): "Credit transaction
frequency/amount shift -> FINANCING_NEED (growth)". Compares the mean
credit (FIN_EVNT_SBTYP_CD='CRD') amount in a trailing window against the
mean in the window immediately before it -- a step change, not a one-off
large payment (that's large_incoming_payment's job; this generator's
synthetic step-change scenario is deliberately distinct from any single
outlier transaction).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ["party_id", "account_id", "posted_at", "amount", "direction"]


@dataclass(frozen=True)
class DetectorConfig:
    window_days: int
    min_credits_per_window: int
    min_change_pct: float
    cooldown_days: int
    rule_version: str
    # SLOT E2 (docs/decision_record.md Tab 6) -- which BaselineModel
    # computes "prior_mean_amount": "deterministic" (default, unchanged --
    # the plain mean of the prior window) or "isolation_forest"
    # (datainsights.ml.baselines.IsolationForestBaseline -- an
    # outlier-robust expected value over the account's credit history, so
    # one past large payment doesn't itself inflate what counts as
    # "prior normal"). Opt-in; see cash_buildup.py's identical option for
    # the full rationale and datainsights/ml/compare_baselines.py for the
    # whole-book comparison this is measured against.
    baseline: str = "deterministic"

    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig":
        d = rules["revenue_pattern_change"]
        return cls(
            window_days=d["window_days"],
            min_credits_per_window=d["min_credits_per_window"],
            min_change_pct=d["min_change_pct"],
            cooldown_days=d["cooldown_days"],
            rule_version=rules["fdm_rule_version"],
            baseline=d.get("baseline", "deterministic"),
        )


DETECTION_COLUMNS = [
    "detection_id", "rule_version", "prty_id", "agrmnt_id", "event_date",
    "detection_as_of", "recent_mean_amount", "prior_mean_amount", "change_pct", "status",
    "baseline",
]

VALID_BASELINES = {"deterministic", "isolation_forest"}


def detect(events: pd.DataFrame, config: DetectorConfig, run_id: str) -> pd.DataFrame:
    """Evaluated at each credit event: compare the mean of credits in
    (event_date - window_days, event_date] against the mean of credits in
    (event_date - 2*window_days, event_date - window_days]. Both windows
    strictly before or at the evaluated row's own date -- no leakage from
    events after it."""
    missing = [c for c in REQUIRED_COLUMNS if c not in events.columns]
    if missing:
        raise ValueError(f"revenue_pattern_change.detect missing required columns: {missing}")
    if config.baseline not in VALID_BASELINES:
        raise ValueError(f"revenue_pattern_change: unknown baseline {config.baseline!r}, "
                         f"must be one of {VALID_BASELINES}")
    credits = events[events["direction"] == "credit"].copy()
    if credits.empty:
        return pd.DataFrame(columns=DETECTION_COLUMNS)

    if config.baseline == "isolation_forest":
        from datainsights.ml.baselines import IsolationForestBaseline
        from datainsights.ml.slots import InsufficientHistory

    credits["posted_at"] = pd.to_datetime(credits["posted_at"])
    now = datetime.now(timezone.utc).isoformat()
    out_rows = []

    for (prty_id, agrmnt_id), grp in credits.groupby(["party_id", "account_id"], sort=False):
        grp = grp.sort_values("posted_at").reset_index(drop=True)
        dates = grp["posted_at"]
        amounts = grp["amount"].to_numpy(dtype=float)
        if config.baseline == "isolation_forest":
            iso_model = IsolationForestBaseline(
                {(agrmnt_id, "credit"): list(zip(dates.dt.date, amounts.tolist()))})

        for i in range(len(grp)):
            d = dates.iloc[i]
            recent_start = d - pd.Timedelta(days=config.window_days)
            prior_start = d - pd.Timedelta(days=2 * config.window_days)

            recent_mask = (dates > recent_start) & (dates <= d)
            prior_mask = (dates > prior_start) & (dates <= recent_start)
            recent_vals = amounts[recent_mask.to_numpy()]
            prior_vals = amounts[prior_mask.to_numpy()]

            if len(recent_vals) < config.min_credits_per_window or \
                    len(prior_vals) < config.min_credits_per_window:
                continue  # not enough evidence either side -- no row emitted
                          # (unlike LIP, we don't persist an
                          # insufficient_evidence row per credit event; that
                          # would be one row per transaction pre-warmup,
                          # unbounded for an active account)

            recent_mean = float(np.mean(recent_vals))
            if config.baseline == "isolation_forest":
                # SLOT E2 opt-in: "prior_mean_amount" becomes an
                # outlier-robust expected value over the account's credit
                # history up to (not including) the prior window's own
                # end -- one past large payment stops inflating what
                # counts as "prior normal". Falls through to
                # not-enough-evidence, same as too few prior_vals.
                try:
                    # Fit on ALL history up to (not including) the recent
                    # window's own start -- IsolationForestBaseline is
                    # designed around a client's whole unlabelled history,
                    # not a fixed-width slice, so this is intentionally
                    # wider than the deterministic path's bounded
                    # prior_start..recent_start window.
                    prior_mean, _tolerance = iso_model.expected(agrmnt_id, "credit", recent_start.date())
                except InsufficientHistory:
                    continue
            else:
                prior_mean = float(np.mean(prior_vals))
            if prior_mean <= 0:
                continue
            change_pct = (recent_mean - prior_mean) / prior_mean
            if abs(change_pct) < config.min_change_pct:
                continue

            out_rows.append({
                "detection_id": f"{config.rule_version}:{agrmnt_id}:{d.date().isoformat()}",
                "rule_version": config.rule_version,
                "prty_id": prty_id,
                "agrmnt_id": agrmnt_id,
                "event_date": d.date().isoformat(),
                "detection_as_of": now,
                "recent_mean_amount": round(recent_mean, 2),
                "prior_mean_amount": round(prior_mean, 2),
                "change_pct": round(change_pct, 4),
                "status": "detected",
                "baseline": config.baseline,
            })

    return pd.DataFrame(out_rows, columns=DETECTION_COLUMNS)


def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame:
    if detections.empty:
        return detections
    out = detections.copy()
    out["event_date_dt"] = pd.to_datetime(out["event_date"])
    for (prty_id, agrmnt_id), grp in out.groupby(["prty_id", "agrmnt_id"]):
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

    direction = "increase" if row["change_pct"] > 0 else "decrease"
    magnitude = clamp_magnitude(abs(row["change_pct"]) / 1.0)  # 100%+ change saturates
    return Signal(
        prty_id=row["prty_id"],
        signal_type="revenue_pattern_change",
        domain="deposits",
        direction=direction,
        magnitude=magnitude,
        observed_date=date_cls.fromisoformat(row["event_date"]),
        evidence_ref=f"EVENT_FINANCIAL:{row['agrmnt_id']}:eff={row['event_date']}",
        source_tables=("EVENT_FINANCIAL",),
        raw_measure={"recent_mean_amount": row["recent_mean_amount"],
                     "prior_mean_amount": row["prior_mean_amount"], "change_pct": row["change_pct"],
                     "baseline": row.get("baseline", "deterministic")},
    )
