"""
FDM-shaped synthetic dataset generator.

Parallel to data_generator/generate_data.py, not a replacement for it --
see docs/decision_record.md's "additive path" rationale in
docs/PROJECT_CONTEXT.md / the approved plan. Produces CSVs matching
config/entities_fdm.yaml under data_generator/output_fdm/ (or
output_fdm_holdout/ for --seed 1337, mirroring the existing dev/holdout
convention).

Reuses generate_data.py's real Eurostat NACE Rev.2 sector map and
country/segment distributions rather than reinventing them -- only the
FDM table/column shape and code_domains.py's value sets are new.

Deliberately small and schema-correctness-focused, not realism/scale
-- this dataset exists to prove FDM join constraints and bi-temporal
as-at correctness locally, ahead of NatWest VDI access, not to be a
production-scale synthetic estate.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
from faker import Faker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data_generator.generate_data import (  # noqa: E402
    SECTORS, SECTOR_NAMES, SECTOR_WEIGHTS, COUNTRIES, COUNTRY_WEIGHTS,
)
from data_generator.fdm import code_domains as cd  # noqa: E402

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
N_PARTY = 60
START_DATE = date(2024, 1, 1)
END_DATE = date(2026, 6, 30)
OUT_DIR_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output_fdm")

SEGMENTS = ["SME", "MID", "LRGE", "INST"]
SEGMENT_WEIGHTS = [0.62, 0.24, 0.09, 0.05]
SEGMENT_BASE_RISK_VAL = {"SME": 6, "MID": 5, "LRGE": 4, "INST": 3}


N_RELATIONSHIP_MANAGERS = 20


def _rm_id_for(prty_id: str) -> str:
    """RM001..RM020, deterministically derived from PRTY_ID via a plain
    hash -- consumes NO rng draw, so adding this never perturbs any other
    field's random stream on regeneration (the same discipline the
    cash_buildup fix in M16 held itself to). Synthetic assignment, not a
    real HR roster -- disclosed in config/entities_fdm.yaml."""
    digest = hashlib.sha256(prty_id.encode()).hexdigest()
    return f"RM{(int(digest, 16) % N_RELATIONSHIP_MANAGERS) + 1:03d}"


def _no_future(d: date) -> date:
    return min(d, END_DATE)


def gen_party(rng: np.random.Generator) -> pd.DataFrame:
    """PARTY, bi-temporal: most parties get one current version; a subset
    get a second, worse-risk-grade version later in the window so as-at
    detectors (collateral_coverage_drop's temporal-invariant test, and any
    future rating_downgrade) have real history to read, not just a
    current-state snapshot."""
    rows = []
    for i in range(N_PARTY):
        prty_id = f"PRTY{i:05d}"
        segment = rng.choice(SEGMENTS, p=SEGMENT_WEIGHTS)
        base_val = SEGMENT_BASE_RISK_VAL[segment] + int(rng.integers(-1, 2))
        base_val = int(np.clip(base_val, 1, 10))
        onboard = START_DATE - timedelta(days=int(rng.integers(30, 900)))

        high_risk = rng.random() < 0.05
        v1_end = None
        downgrades = rng.random() < 0.20
        if downgrades:
            change_date = START_DATE + timedelta(
                days=int(rng.integers(30, (END_DATE - START_DATE).days - 30))
            )
            v1_end = change_date

        rows.append({
            "PRTY_ID": prty_id,
            "EFFECTIVE_START_DT": onboard.isoformat(),
            "EFFECTIVE_END_DT": v1_end.isoformat() if v1_end else "",
            "PRTY_TYP_CD": cd.PRTY_TYP_CD.values[0],
            "PRTY_SBTYP_CD": rng.choice(cd.PRTY_SBTYP_CD.values),
            "RSK_GRD_CD": cd.RSK_GRD_CD.values[base_val - 1],
            "RSK_GRD_VAL": base_val,
            "RSK_GRD_DT": onboard.isoformat(),
            "PRTY_SGMNT_CD": segment,
            "HIGH_RSK_CUST_IND": cd.IND_TRUE if high_risk else cd.IND_FALSE,
            "RLTNSHP_MGR_ID": _rm_id_for(prty_id),
        })

        if downgrades:
            worse_val = int(np.clip(base_val + int(rng.integers(1, 3)), 1, 10))
            rows.append({
                "PRTY_ID": prty_id,
                "EFFECTIVE_START_DT": v1_end.isoformat(),
                "EFFECTIVE_END_DT": "",
                "PRTY_TYP_CD": cd.PRTY_TYP_CD.values[0],
                "PRTY_SBTYP_CD": rows[-1]["PRTY_SBTYP_CD"],
                "RSK_GRD_CD": cd.RSK_GRD_CD.values[worse_val - 1],
                "RSK_GRD_VAL": worse_val,
                "RSK_GRD_DT": v1_end.isoformat(),
                "PRTY_SGMNT_CD": segment,
                "HIGH_RSK_CUST_IND": cd.IND_TRUE if high_risk else cd.IND_FALSE,
                "RLTNSHP_MGR_ID": _rm_id_for(prty_id),  # same RM as v1 -- risk grade changes, RM doesn't
            })
    return pd.DataFrame(rows)


def gen_party_demographic(rng: np.random.Generator, party_ids: list[str]) -> pd.DataFrame:
    rows = []
    for prty_id in party_ids:
        idx = rng.choice(len(SECTOR_NAMES), p=SECTOR_WEIGHTS)
        sector_name = SECTOR_NAMES[idx]
        section, codes = SECTORS[sector_name]
        rows.append({
            "PRTY_ID": prty_id,
            "NACE_SECTION_CD": section,
            "NACE_CODE": rng.choice(codes),
            "SECTOR_NM": sector_name,
        })
    return pd.DataFrame(rows)


def gen_party_locator(rng: np.random.Generator, party_ids: list[str]) -> pd.DataFrame:
    rows = [
        {"PRTY_ID": p, "COUNTRY_CD": rng.choice(COUNTRIES, p=COUNTRY_WEIGHTS)}
        for p in party_ids
    ]
    return pd.DataFrame(rows)


def gen_agreements_and_children(rng: np.random.Generator, party_ids: list[str]):
    """For each party: one DEP (current account) agreement always; a
    credit facility (LON/ODR) for ~55%; a mortgage for ~15%. Returns
    (agreement, party_agreement, daily_balance, mortgage_agreement,
    collateral_item, agreement_collateral_item, collateral_item_value,
    dormant_dep_agreement_ids)."""
    agreements, party_agreements, daily_balances = [], [], []
    mortgages, coll_items, agr_coll, coll_values = [], [], [], []
    dormant_dep_ids: set[str] = set()
    n_agr, n_coll = 0, 0

    for prty_id in party_ids:
        # --- deposit / current account (always) ---
        dep_id = f"AGR{n_agr:06d}"
        n_agr += 1
        open_dt = START_DATE - timedelta(days=int(rng.integers(60, 700)))
        agreements.append({
            "AGRMNT_ID": dep_id, "EFFECTIVE_START_DT": open_dt.isoformat(),
            "EFFECTIVE_END_DT": "", "AGRMNT_OPEN_DT": open_dt.isoformat(),
            "AGRMNT_CLOSE_DT": "", "AGRMNT_TYP_CD": "DEP",
            "AGRMNT_SBTYP_CD": "CUR", "AGRMNT_ORIG_LIM": "",
            "AGRMNT_PURP_CD": "GENERAL",
        })
        party_agreements.append({
            "PRTY_ID": prty_id, "AGRMNT_ID": dep_id,
            "PRTY_AGRMNT_ROLE_CD": cd.PRTY_AGRMNT_ROLE_CD.values[0],
        })
        bal = float(rng.uniform(20_000, 400_000))
        bal_start = bal  # plain assignment -- consumes no rng draw
        buildup = rng.random() < 0.15  # feeds cash_buildup detector
        dormant = rng.random() < 0.08  # feeds dormancy detector
        if dormant:
            dormant_dep_ids.add(dep_id)
        d = START_DATE
        while d <= END_DATE:
            drift = float(rng.normal(0, bal * 0.01))
            # A BOUNDED buildup episode, strong enough to actually clear
            # cash_buildup's gate. The previous 0.003/week ramp topped out
            # at a 14.87% rise over the detector's 60-day window against a
            # min_increase_pct of 0.15 -- measured across both the dev and
            # the 300-party scaled set, ZERO accounts ever crossed, so
            # TREASURY_OPPORTUNITY could never fire. Fixed window (no extra
            # rng draw) so every other entity's random stream is unchanged.
            if buildup and d > END_DATE - timedelta(days=400) and bal < bal_start * 3:
                drift += bal * 0.02
            bal = max(1000.0, bal + drift)
            daily_balances.append({
                "AGRMNT_ID": dep_id, "AGRMNT_DLY_BAL_STRT_DTTM": d.isoformat(),
                "AGRMNT_LDGR_BAL_AMT": round(bal, 2), "AGRMNT_BAL_CURY_CD": "EUR",
            })
            d += timedelta(days=7)  # weekly snapshot keeps row count sane

        # --- credit facility for ~55% ---
        if rng.random() < 0.55:
            fac_id = f"AGR{n_agr:06d}"
            n_agr += 1
            typ = rng.choice(["LON", "ODR"])
            orig_lim = float(rng.choice([100_000, 250_000, 500_000, 1_000_000]))
            close_dt = _no_future(open_dt + timedelta(days=int(rng.integers(700, 1800))))
            maturity_soon = rng.random() < 0.15  # feeds facility_maturity_approaching
            if maturity_soon:
                close_dt = END_DATE - timedelta(days=int(rng.integers(1, 89)))
            agreements.append({
                "AGRMNT_ID": fac_id, "EFFECTIVE_START_DT": open_dt.isoformat(),
                "EFFECTIVE_END_DT": "", "AGRMNT_OPEN_DT": open_dt.isoformat(),
                "AGRMNT_CLOSE_DT": close_dt.isoformat(), "AGRMNT_TYP_CD": typ,
                "AGRMNT_SBTYP_CD": "RCF" if typ == "LON" else "TLN",
                "AGRMNT_ORIG_LIM": orig_lim,
                "AGRMNT_PURP_CD": rng.choice(cd.AGRMNT_PURP_CD.values),
            })
            party_agreements.append({
                "PRTY_ID": prty_id, "AGRMNT_ID": fac_id,
                "PRTY_AGRMNT_ROLE_CD": cd.PRTY_AGRMNT_ROLE_CD.values[0],
            })
            spike = rng.random() < 0.15  # feeds facility_utilization_spike
            util = float(rng.uniform(0.1, 0.5))
            d = START_DATE
            while d <= END_DATE:
                if spike and d > END_DATE - timedelta(days=30):
                    util = min(0.98, util + 0.05)
                elif not spike:
                    util = float(np.clip(util + rng.normal(0, 0.01), 0.05, 0.7))
                daily_balances.append({
                    "AGRMNT_ID": fac_id, "AGRMNT_DLY_BAL_STRT_DTTM": d.isoformat(),
                    "AGRMNT_LDGR_BAL_AMT": round(util * orig_lim, 2),
                    "AGRMNT_BAL_CURY_CD": "EUR",
                })
                d += timedelta(days=7)

            # collateral on ~40% of credit facilities
            if rng.random() < 0.40:
                item_id = f"CLT{n_coll:05d}"
                n_coll += 1
                coll_items.append({
                    "CLTRL_ITEM_ID": item_id,
                    "CLTRL_ITEM_TYP_CD": rng.choice(cd.CLTRL_ITEM_TYP_CD.values),
                })
                agr_coll.append({"AGRMNT_ID": fac_id, "CLTRL_ITEM_ID": item_id})
                val = orig_lim * float(rng.uniform(1.1, 1.6))
                coverage_drop = rng.random() < 0.20  # feeds collateral_coverage_drop
                v_start = open_dt
                v_end = None
                if coverage_drop:
                    v_end = START_DATE + timedelta(days=int(rng.integers(60, 500)))
                coll_values.append({
                    "CLTRL_ITEM_ID": item_id, "EFFECTIVE_START_DT": v_start.isoformat(),
                    "EFFECTIVE_END_DT": v_end.isoformat() if v_end else "",
                    "CLTRL_VAL_AMT": round(val, 2),
                    "CLTRL_VALUTN_MTHD_CD": rng.choice(cd.CLTRL_VALUTN_MTHD_CD.values),
                })
                if coverage_drop:
                    dropped_val = val * float(rng.uniform(0.5, 0.75))
                    coll_values.append({
                        "CLTRL_ITEM_ID": item_id, "EFFECTIVE_START_DT": v_end.isoformat(),
                        "EFFECTIVE_END_DT": "",
                        "CLTRL_VAL_AMT": round(dropped_val, 2),
                        "CLTRL_VALUTN_MTHD_CD": rng.choice(cd.CLTRL_VALUTN_MTHD_CD.values),
                    })

        # --- mortgage for ~15% ---
        if rng.random() < 0.15:
            mort_id = f"AGR{n_agr:06d}"
            n_agr += 1
            fixed_expiring_soon = rng.random() < 0.20  # feeds fixed_rate_expiry
            fxd_end = (END_DATE - timedelta(days=int(rng.integers(1, 89)))
                       if fixed_expiring_soon
                       else END_DATE + timedelta(days=int(rng.integers(90, 900))))
            agreements.append({
                "AGRMNT_ID": mort_id, "EFFECTIVE_START_DT": open_dt.isoformat(),
                "EFFECTIVE_END_DT": "", "AGRMNT_OPEN_DT": open_dt.isoformat(),
                "AGRMNT_CLOSE_DT": (open_dt + timedelta(days=365 * 20)).isoformat(),
                "AGRMNT_TYP_CD": "MTG", "AGRMNT_SBTYP_CD": "RCF",
                "AGRMNT_ORIG_LIM": float(rng.choice([250_000, 500_000, 750_000])),
                "AGRMNT_PURP_CD": "PROPERTY",
            })
            party_agreements.append({
                "PRTY_ID": prty_id, "AGRMNT_ID": mort_id,
                "PRTY_AGRMNT_ROLE_CD": cd.PRTY_AGRMNT_ROLE_CD.values[0],
            })
            mortgages.append({"AGRMNT_ID": mort_id, "MORT_FXED_RT_END_DT": fxd_end.isoformat()})

    return (pd.DataFrame(agreements), pd.DataFrame(party_agreements),
            pd.DataFrame(daily_balances), pd.DataFrame(mortgages),
            pd.DataFrame(coll_items), pd.DataFrame(agr_coll), pd.DataFrame(coll_values),
            dormant_dep_ids)


def gen_event_financial(rng: np.random.Generator, dep_agreement_ids: list[str], dormant_ids: set) -> pd.DataFrame:
    """Transactions on deposit-type agreements only, weekly-ish cadence,
    with a subset showing a step change in frequency/amount (feeds
    revenue_pattern_change) and a subset going silent (feeds dormancy)."""
    rows = []
    n_evt = 0
    for agr_id in dep_agreement_ids:
        if agr_id in dormant_ids:
            last_active = START_DATE + timedelta(days=int(rng.integers(30, 90)))
            n_tx = int(rng.integers(3, 8))
            for _ in range(n_tx):
                d = START_DATE + timedelta(days=int(rng.integers(0, (last_active - START_DATE).days + 1)))
                rows.append(_tx_row(agr_id, d, rng, n_evt)); n_evt += 1
            continue

        step_change = rng.random() < 0.15
        base_freq_days = 5
        base_amt_scale = float(rng.uniform(1000, 8000))
        d = START_DATE
        while d <= END_DATE:
            amt_scale = base_amt_scale
            if step_change and d > START_DATE + timedelta(days=(END_DATE - START_DATE).days // 2):
                amt_scale *= 2.2
            rows.append(_tx_row(agr_id, d, rng, n_evt, amt_scale)); n_evt += 1
            d += timedelta(days=base_freq_days + int(rng.integers(-2, 3)))
    return pd.DataFrame(rows)


def _tx_row(agr_id, d, rng, n_evt, amt_scale=None):
    amt_scale = amt_scale or float(rng.uniform(500, 5000))
    is_credit = rng.random() < 0.5
    return {
        "EVNT_ID": f"EVT{n_evt:07d}", "AGRMNT_ID_TRN_ACCT": agr_id,
        "FIN_EVNT_PSTD_DT": d.isoformat(),
        "FIN_EVNT_AMT": round(float(rng.uniform(0.4, 1.6)) * amt_scale, 2),
        "FIN_EVNT_CURY_CD": "EUR",
        "FIN_EVNT_SBTYP_CD": "CRD" if is_credit else "DBT",
        "FIN_EVNT_TYP_ID": rng.choice(cd.INFLOW_TYPE_IDS if is_credit else cd.OUTFLOW_TYPE_IDS),
    }


def main():
    global N_PARTY, START_DATE, END_DATE

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", default=None)
    # M8/Step 3 (docs/agentic_plan.md): scale knobs, both optional and
    # defaulting to the original hardcoded values (N_PARTY=60,
    # START_DATE=2024-01-01) -- every existing caller (both demos, every
    # test with a --seed 42/1337 dependency) is unaffected unless one of
    # these is passed explicitly.
    parser.add_argument("--n-parties", type=int, default=N_PARTY,
                        help=f"number of synthetic parties to generate (default {N_PARTY})")
    parser.add_argument("--history-years", type=float, default=None,
                        help="override history length in years (default: the original "
                             "2024-01-01..2026-06-30 window, ~2.5 years)")
    args = parser.parse_args()

    N_PARTY = args.n_parties
    if args.history_years is not None:
        START_DATE = END_DATE - timedelta(days=int(args.history_years * 365.25))

    out_dir = args.out_dir or (
        OUT_DIR_DEFAULT if args.seed == 42
        else OUT_DIR_DEFAULT + "_holdout"
    )
    os.makedirs(out_dir, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    Faker.seed(args.seed)

    party = gen_party(rng)
    party_ids = sorted(party["PRTY_ID"].unique())
    demographic = gen_party_demographic(rng, party_ids)
    locator = gen_party_locator(rng, party_ids)

    (agreement, party_agreement, daily_balance, mortgage,
     coll_item, agr_coll, coll_value,
     dormant_dep_ids) = gen_agreements_and_children(rng, party_ids)

    dep_ids = agreement.loc[agreement["AGRMNT_TYP_CD"] == "DEP", "AGRMNT_ID"].tolist()
    event_financial = gen_event_financial(rng, dep_ids, dormant_dep_ids)

    # Domain-segregated output -- mirrors config/entities_fdm.yaml's
    # `domain:` tag on each entity (kernel = Tier 1 FDM classes used
    # everywhere; lending = Tier 3 domain-specific extensions). This is
    # the same split FdmLocalSource resolves paths against, so a real
    # Snowflake source honoring the same `domain` field later maps this
    # to a schema/database, not a folder -- zero change above that layer.
    kernel_tables = {
        "party": party, "party_demographic": demographic, "party_locator": locator,
        "agreement": agreement, "party_agreement": party_agreement,
        "agreement_daily_balance": daily_balance, "event_financial": event_financial,
    }
    lending_tables = {
        "mortgage_agreement": mortgage, "collateral_item": coll_item,
        "agreement_collateral_item": agr_coll, "collateral_item_value": coll_value,
    }
    for domain, tables in (("kernel", kernel_tables), ("lending", lending_tables)):
        domain_dir = os.path.join(out_dir, domain)
        os.makedirs(domain_dir, exist_ok=True)
        print(f"-- {domain}/ --")
        for name, df in tables.items():
            df.to_csv(os.path.join(domain_dir, f"{name}.csv"), index=False)
            print(f"  {name:28s} {len(df):>8,} rows")
    print(f"Written to {out_dir}/{{kernel,lending}}/")


if __name__ == "__main__":
    main()
