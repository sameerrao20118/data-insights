"""
A runnable, visible demonstration of the Phase 1 FOUNDATION
(docs/generalization_plan.md) -- what CanonicalSource can already do,
side by side with the physical access every detector still actually
uses today. Nothing in the live pipeline calls this; it exists so the
foundation is witnessable from the terminal, not just from a passing
test file.

Run: python -m datainsights.semantic.demo_canonical_read
"""

from __future__ import annotations

import os
from datetime import date

from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.fdm_local import FdmLocalSource

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT_PATH = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")


def main():
    if not os.path.isdir(FDM_DIR):
        raise SystemExit("Generate FDM data first: python -m data_generator.fdm.generate_fdm")

    source = FdmLocalSource(FDM_DIR, CONTRACT_PATH)
    canonical = CanonicalSource(source, load_binding("fdm"))
    as_of = date(2025, 10, 4)
    prty_id = "PRTY00036"

    print("=" * 78)
    print("SAME underlying data, two ways to read it")
    print("=" * 78)

    print("\n--- TODAY: what detection_engine/*.py and agents/tools.py actually do ---")
    print("    (direct FdmLocalSource call, physical FDM column names)")
    party_df = source.party(as_of)
    row = party_df[party_df["PRTY_ID"] == prty_id].iloc[0]
    print(f"    source.party(as_of) -> row['PRTY_SGMNT_CD'] = {row['PRTY_SGMNT_CD']!r}")
    print(f"                           row['HIGH_RSK_CUST_IND'] = {row['HIGH_RSK_CUST_IND']!r}")
    print("    -> to get sector/country you ALSO need party_demographic() and")
    print("       party_locator() and a manual merge -- see fdm_worklist.py:147-157")

    print("\n--- FOUNDATION BUILT, NOT YET WIRED IN: the same client through CanonicalSource ---")
    print("    (schema-agnostic call, canonical field names, joins already applied)")
    party = canonical.read("Party", as_at=as_of, party_id=prty_id).iloc[0]
    print("    canonical.read('Party', as_at=as_of, party_id=prty_id) ->")
    print(f"        segment       = {party['segment']!r}")
    print(f"        sector_code   = {party['sector_code']!r}   (was a separate PARTY_DEMOGRAPHIC join)")
    print(f"        sector_name   = {party['sector_name']!r}")
    print(f"        country_code  = {party['country_code']!r}   (was a separate PARTY_LOCATOR join)")
    print(f"        high_risk_flag = {bool(party['high_risk_flag'])!r}   (already a real bool, not the string 'Y'/'N')")

    print("\n--- The point: this same call works whether the physical schema is FDM,")
    print("    the legacy schema, or a schema that doesn't exist yet -- IF a binding")
    print("    exists for it. Only config/bindings/fdm.yaml exists today.")

    print("\n" + "=" * 78)
    print("What a detector would look like AFTER the rewire (not real code -- illustration)")
    print("=" * 78)
    accounts = canonical.read("Account", as_at=as_of, party_id=prty_id)
    deposit = accounts[accounts["product_class"] == "deposit"]
    if not deposit.empty:
        acc_id = deposit.iloc[0]["account_id"]
        balances = canonical.read("BalanceObservation", account_id=acc_id)
        print("\n  Today,  detection_engine/cash_buildup.py requires the column")
        print("          'AGRMNT_LDGR_BAL_AMT' by name (see its REQUIRED_COLUMNS).")
        print("  After the rewire, it would require 'balance' instead, and read via")
        print("          canonical.read('BalanceObservation', account_id=...) --")
        print(f"          {len(balances)} rows, same real numbers, schema-neutral name:")
        print(balances.tail(3).to_string(index=False))
        print("\n  That one rename (+ the equivalent in agents/tools.py) is what makes")
        print("  cash_buildup.py stop caring whether 'balance' came from FDM's")
        print("  AGRMNT_LDGR_BAL_AMT, the legacy schema's own balance column, or a")
        print("  third schema nobody has written yet -- as long as ITS binding maps")
        print("  'balance' to whatever that schema calls it.")

    print("\n" + "=" * 78)
    print("Honest status: this script is the only place in the repo that calls")
    print("CanonicalSource today. demo_fdm_scenario.py, agents/tools.py, and every")
    print("detector still use the LEFT-hand side above. Nothing you see in the")
    print("dashboard or the worklist is produced by the right-hand side yet.")
    print("=" * 78)


if __name__ == "__main__":
    main()
