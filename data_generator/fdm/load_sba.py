"""
Builds a third demo schema, deliberately different from both `fdm` and
`legacy`: REAL commercial entities/sectors/loans (U.S. Small Business
Administration PPP loan-level data, data_generator/external/fetch_sba.py)
with SYNTHETIC deposit-account activity layered on top, because no
public source anywhere discloses real transaction/balance history for a
real commercial client -- that's a confidentiality constraint on the
whole data category, not a gap in this build.

What's REAL, used as-is: borrower legal name, NAICS industry sector code,
US state, business type, loan amount, loan approval date, loan status
(paid in full / charged off). What's SYNTHETIC, generated here: the one
deposit account per party, its balance history, and its transactions --
same generation techniques data_generator/fdm/generate_fdm.py already
uses (random-walk balances, weekly cadence, a cash-buildup subset, a
dormant subset, a step-change subset for revenue_pattern_change),
disclosed in every output.

Deliberately real-sector-diverse: the whole point of this dataset is
proving the platform is NOT locked to one industry -- unlike Berka
(retail personal banking, ruled out), this sample spans construction,
healthcare, professional services, manufacturing, retail, hospitality,
and more, because that's what the real SBA data actually contains.

Physical output matches config/entities_sba.yaml's contract: party.csv,
agreement.csv (real loan rows + synthetic deposit rows, same shape),
balance.csv, transaction.csv -- read by OfflineLocalSource, mapped onto
the canonical model by config/bindings/sba.yaml. No new Python needed
anywhere else in the pipeline; see that binding's own docstring.

Run:
  python -m data_generator.external.fetch_sba   # once, ~450MB real download
  python -m data_generator.fdm.load_sba --seed 42
"""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
SBA_RAW_PATH = os.path.join(REPO_ROOT, "data_generator", "external", "sba", "public_150k_plus.csv")
OUT_DIR_DEFAULT = os.path.join(REPO_ROOT, "data_generator", "output_fdm_sba")

N_PARTY = 400
# The synthetic deposit-activity window -- independent of the loans' own
# real (2020-2021) approval dates, which stay real and historical.
DEP_START = date(2025, 1, 1)
DEP_END = date(2026, 3, 1)

USABLE_STATUSES = {"Paid in Full", "Charged Off"}


def load_real_loans(n_party: int, seed: int) -> pd.DataFrame:
    cols = ["LoanNumber", "DateApproved", "BorrowerName", "BorrowerState", "LoanStatus",
           "LoanStatusDate", "InitialApprovalAmount", "NAICSCode", "BusinessType"]
    df = pd.read_csv(SBA_RAW_PATH, encoding="latin-1", usecols=cols)
    df = df[df["LoanStatus"].isin(USABLE_STATUSES)]
    df = df.dropna(subset=["NAICSCode", "BorrowerName", "DateApproved", "BorrowerState"])
    df["NAICSCode"] = df["NAICSCode"].astype(int).astype(str).str[:2]  # 2-digit NAICS sector
    return df.sample(n=min(n_party, len(df)), random_state=seed).reset_index(drop=True)


def _synthetic_deposit_history(rng: np.random.Generator, dep_id: str, scale: float
                               ) -> tuple[list[dict], list[dict]]:
    """One deposit account's balance history + transactions, same
    generation techniques as data_generator/fdm/generate_fdm.py's
    gen_agreements_and_children / gen_event_financial -- a cash-buildup
    subset, a dormant subset, a step-change subset. `scale` (derived from
    the party's real loan size) sets the account's rough activity level,
    so a company with a $2M loan doesn't show $500 balances."""
    balances, transactions = [], []
    bal = float(rng.uniform(0.05, 0.25) * scale)
    buildup = rng.random() < 0.15
    dormant = rng.random() < 0.08
    step_change = rng.random() < 0.15

    d = DEP_START
    while d <= DEP_END:
        drift = float(rng.normal(0, bal * 0.01))
        if buildup and d > DEP_START + timedelta(days=(DEP_END - DEP_START).days // 2):
            drift += bal * 0.003
        bal = max(1000.0, bal + drift)
        balances.append({"account_id": dep_id, "observed_at": d.isoformat(), "balance": round(bal, 2)})
        d += timedelta(days=7)

    n_tx = 0
    if dormant:
        last_active = DEP_START + timedelta(days=int(rng.integers(30, 90)))
        for _ in range(int(rng.integers(3, 8))):
            td = DEP_START + timedelta(days=int(rng.integers(0, (last_active - DEP_START).days + 1)))
            transactions.append(_tx_row(dep_id, td, rng, n_tx, scale)); n_tx += 1
        return balances, transactions

    base_amt = float(rng.uniform(0.02, 0.1) * scale)
    d = DEP_START
    while d <= DEP_END:
        amt_scale = base_amt
        if step_change and d > DEP_START + timedelta(days=(DEP_END - DEP_START).days // 2):
            amt_scale *= 2.2
        transactions.append(_tx_row(dep_id, d, rng, n_tx, amt_scale)); n_tx += 1
        d += timedelta(days=5 + int(rng.integers(-2, 3)))
    return balances, transactions


def _tx_row(account_id: str, d: date, rng: np.random.Generator, n: int, amt_scale: float) -> dict:
    is_credit = rng.random() < 0.5
    amt = round(float(rng.uniform(0.4, 1.6)) * max(amt_scale, 50.0), 2)
    return {"transaction_id": f"TXSBA{n:07d}_{account_id}", "account_id": account_id,
           "posted_at": d.isoformat(), "amount": amt,
           "direction": "credit" if is_credit else "debit", "currency": "USD"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-parties", type=int, default=N_PARTY)
    parser.add_argument("--out-dir", default=OUT_DIR_DEFAULT)
    args = parser.parse_args()

    if not os.path.exists(SBA_RAW_PATH):
        raise SystemExit(
            "Real SBA data not found. Run first:\n  python -m data_generator.external.fetch_sba"
        )

    rng = np.random.default_rng(args.seed)
    loans = load_real_loans(args.n_parties, args.seed)

    parties, agreements, balances, transactions = [], [], [], []
    for i, row in loans.iterrows():
        party_id = f"SBA{i:05d}"
        loan_id = f"LOANSBA{i:05d}"
        dep_id = f"DEPSBA{i:05d}"

        parties.append({
            "party_id": party_id, "legal_name": row["BorrowerName"], "naics_code": row["NAICSCode"],
            "state": row["BorrowerState"], "business_type": row["BusinessType"] or "Unknown",
            "opened_at": row["DateApproved"],
        })

        close_dt = row["LoanStatusDate"] if row["LoanStatus"] == "Paid in Full" and pd.notna(row["LoanStatusDate"]) else ""
        agreements.append({
            "agreement_id": loan_id, "party_id": party_id, "product_type_cd": "PPP_LOAN",
            "orig_limit": row["InitialApprovalAmount"], "open_dt": row["DateApproved"],
            "close_dt": close_dt, "status": row["LoanStatus"],
        })
        agreements.append({
            "agreement_id": dep_id, "party_id": party_id, "product_type_cd": "DEP",
            "orig_limit": "", "open_dt": row["DateApproved"], "close_dt": "", "status": "",
        })

        bal_rows, tx_rows = _synthetic_deposit_history(rng, dep_id, float(row["InitialApprovalAmount"]))
        balances.extend(bal_rows)
        transactions.extend(tx_rows)

    os.makedirs(args.out_dir, exist_ok=True)
    pd.DataFrame(parties).to_csv(os.path.join(args.out_dir, "party.csv"), index=False)
    pd.DataFrame(agreements).to_csv(os.path.join(args.out_dir, "agreement.csv"), index=False)
    pd.DataFrame(balances).to_csv(os.path.join(args.out_dir, "balance.csv"), index=False)
    pd.DataFrame(transactions).to_csv(os.path.join(args.out_dir, "transaction.csv"), index=False)

    print(f"Wrote {len(parties)} real SBA-borrower parties, {len(agreements)} agreements "
         f"({len(parties)} real loans + {len(parties)} synthetic deposit accounts), "
         f"{len(balances)} balance snapshots, {len(transactions)} synthetic transactions "
         f"-> {args.out_dir}")
    print(f"Sector spread (NAICS 2-digit): {sorted(loans['NAICSCode'].unique())}")


if __name__ == "__main__":
    main()
