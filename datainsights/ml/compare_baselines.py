"""
Whole-book comparison: DeterministicBaseline vs. IsolationForestBaseline
(SLOT E2), run through the REAL detectors (cash_buildup,
revenue_pattern_change) against the REAL generated FDM dataset -- not the
synthetic injection harness in evaluate_baselines.py. That harness proves
"can the challenger detect an anomaly I planted"; this script answers a
different, more practical question: "on the data this pipeline actually
runs against today, where do the two baselines disagree, and what does
that cost?"

Evaluates ONE point per agreement (the most recent daily-balance /
credit-event row as of `as_of`), not every historical row -- this matches
how agents/tools.py actually calls these detectors in production (one
evaluation per client per run), and keeps runtime reasonable. A full
historical backtest (every row, both baselines) is possible with the same
detect() functions but is measurably expensive -- IsolationForestBaseline
refits a fresh sklearn model per evaluated point (no caching across rows
by design, so each point's fit only ever sees data legitimately available
as-at that point). See the runtime note this script prints; a whole-book
HISTORICAL backtest at that cost is a real, unresolved scaling question
this pass does not attempt to fix -- see docs/current_state.md.

Run: python -m datainsights.ml.compare_baselines
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from detection_engine import cash_buildup, revenue_pattern_change

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
RULES_PATH = os.path.join(REPO_ROOT, "config", "rules.yaml")


@dataclass
class Row:
    prty_id: str
    agrmnt_id: str
    det_status: str
    iso_status: str

    @property
    def agrees(self) -> bool:
        # "detected" vs "not_detected"/"insufficient_evidence" is the
        # operationally meaningful line -- both non-detected variants mean
        # "no signal reaches the RM", so treat them as agreement with each
        # other, disagreement only against "detected".
        return (self.det_status == "detected") == (self.iso_status == "detected")


def _last_row_per_agreement(result, id_col: str):
    """The single most recent evaluated row per agreement -- matches
    agents/tools.py's `.iloc[-1]` convention (one live status per client
    per run), not a full historical trace."""
    if result.empty:
        return result
    return result.sort_values("event_date").groupby(id_col, as_index=False).tail(1)


def _canonical(source, binding_name: str) -> CanonicalSource:
    return CanonicalSource(source, load_binding(binding_name))


def compare_cash_buildup(source, rules: dict, as_of: date, binding_name: str = "fdm") -> list[Row]:
    """Whole book in ONE canonical read per concept (R1/R3/R13,
    docs/refactor_plan.md) -- no FDM-only source methods, so this now
    runs against any bound schema, not just fdm."""
    det_cfg = cash_buildup.DetectorConfig.from_rules_dict(rules)
    iso_cfg = cash_buildup.DetectorConfig(**{**det_cfg.__dict__, "baseline": "isolation_forest"})
    canonical = _canonical(source, binding_name)

    accounts = canonical.read("Account", as_at=as_of)
    deposits = accounts[accounts["product_class"] == "deposit"][["account_id", "party_id"]]
    balances = canonical.read("BalanceObservation")
    balances = balances.merge(deposits, on="account_id")
    ts = pd.to_datetime(balances["observed_at"])
    balances = balances[(ts >= pd.Timestamp(as_of - timedelta(days=det_cfg.window_days * 2)))
                        & (ts <= pd.Timestamp(as_of))]

    rows = []
    for account_id, grp in balances.groupby("account_id"):
        det_last = _last_row_per_agreement(cash_buildup.detect(grp, det_cfg, "compare"), "agrmnt_id")
        iso_last = _last_row_per_agreement(cash_buildup.detect(grp, iso_cfg, "compare"), "agrmnt_id")
        prty_id = grp["party_id"].iloc[0]
        det_status = det_last.iloc[0]["status"] if not det_last.empty else "not_detected"
        iso_status = iso_last.iloc[0]["status"] if not iso_last.empty else "not_detected"
        rows.append(Row(prty_id, account_id, det_status, iso_status))
    return rows


def compare_revenue_pattern_change(source, rules: dict, as_of: date, binding_name: str = "fdm") -> list[Row]:
    det_cfg = revenue_pattern_change.DetectorConfig.from_rules_dict(rules)
    iso_cfg = revenue_pattern_change.DetectorConfig(**{**det_cfg.__dict__, "baseline": "isolation_forest"})
    canonical = _canonical(source, binding_name)

    accounts = canonical.read("Account", as_at=as_of)
    deposits = accounts[accounts["product_class"] == "deposit"][["account_id", "party_id"]]
    events = canonical.read("Transaction").merge(deposits, on="account_id")
    ts = pd.to_datetime(events["posted_at"])
    events = events[(ts >= pd.Timestamp(as_of - timedelta(days=det_cfg.window_days * 3)))
                    & (ts <= pd.Timestamp(as_of))]

    rows = []
    for account_id, grp in events.groupby("account_id"):
        det_last = _last_row_per_agreement(revenue_pattern_change.detect(grp, det_cfg, "compare"), "agrmnt_id")
        iso_last = _last_row_per_agreement(revenue_pattern_change.detect(grp, iso_cfg, "compare"), "agrmnt_id")
        prty_id = grp["party_id"].iloc[0]
        det_status = det_last.iloc[0]["status"] if not det_last.empty else "not_detected"
        iso_status = iso_last.iloc[0]["status"] if not iso_last.empty else "not_detected"
        rows.append(Row(prty_id, account_id, det_status, iso_status))
    return rows


def _report(name: str, rows: list[Row]) -> None:
    det_detected = sum(r.det_status == "detected" for r in rows)
    iso_detected = sum(r.iso_status == "detected" for r in rows)
    disagreements = [r for r in rows if not r.agrees]
    print(f"\n--- {name} -- {len(rows)} agreements evaluated ---")
    print(f"  deterministic detected: {det_detected}")
    print(f"  isolation_forest detected: {iso_detected}")
    print(f"  disagree: {len(disagreements)}")
    for r in disagreements[:10]:
        print(f"    {r.prty_id} / {r.agrmnt_id}: deterministic={r.det_status!r} isolation_forest={r.iso_status!r}")
    if len(disagreements) > 10:
        print(f"    ... and {len(disagreements) - 10} more")


def main():
    if not os.path.isdir(FDM_DIR):
        raise SystemExit("Generate FDM data first: python -m data_generator.fdm.generate_fdm")
    rt = build_runtime("fdm_local")
    rules, source = rt.rules, rt.source
    as_of = date(2025, 10, 4)

    print("=" * 78)
    print("SLOT E2 whole-book comparison -- real detectors, real generated data")
    print("One evaluation point per agreement (latest, as agents/tools.py uses it) --")
    print("NOT a full historical backtest. Synthetic data; NOT a real-world result.")
    print("=" * 78)

    t0 = time.time()
    cb_rows = compare_cash_buildup(source, rules, as_of)
    t1 = time.time()
    rp_rows = compare_revenue_pattern_change(source, rules, as_of)
    t2 = time.time()

    _report("cash_buildup", cb_rows)
    _report("revenue_pattern_change", rp_rows)

    print(f"\nRuntime: cash_buildup {t1 - t0:.1f}s, revenue_pattern_change {t2 - t1:.1f}s "
          f"(isolation_forest refits per evaluated point -- see this module's docstring; "
          f"a full historical backtest at this cost would need a caching optimization not built this pass).")


if __name__ == "__main__":
    main()
