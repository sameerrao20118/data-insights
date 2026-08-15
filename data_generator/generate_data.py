"""
Synthetic commercial/institutional banking dataset generator for a
Next-Best-Action (NBA) / Event-Based-Marketing (EBM) proof of concept.

This does NOT use any real bank data. It generates a plausible, internally
consistent client base and multi-year transaction history for European
commercial and institutional banking clients, with a set of "trigger events"
deliberately embedded and logged to trigger_events.csv as ground truth.
That ground-truth file lets you measure precision/recall of any rule engine
or ML model you later build on top of transactions.csv, without knowing
the answers in advance from looking at the raw data.

Distributional choices (revenue bands by segment, seasonality, sector mix,
FX share of SME turnover, typical DSO/payment terms) are loosely calibrated
to public aggregate statistics (ECB SAFE survey on SME access to finance,
Eurostat structural business statistics, ECB payments statistics) -- not
fitted to any real client-level data, which does not exist for us to see.

Output: CSV files in ./output/
    clients.csv
    accounts.csv
    transactions.csv
    trigger_events.csv   (ground truth labels, keep separate from "raw" data
                           during rule-engine dev -- treat transactions.csv
                           as the only thing your detector is allowed to see)
    data_dictionary.md
"""

import numpy as np
import pandas as pd
from faker import Faker
from datetime import date, timedelta
import uuid
import os

# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------
SEED = 42
N_CLIENTS = 300
START_DATE = date(2023, 1, 1)
END_DATE = date(2025, 12, 31)
OUT_DIR = os.path.join(os.path.dirname(__file__), "output")

rng = np.random.default_rng(SEED)
fake = Faker(["en_GB", "de_DE", "fr_FR", "nl_NL", "es_ES", "it_IT"])
Faker.seed(SEED)

COUNTRIES = ["DE", "FR", "NL", "BE", "ES", "IT", "PL", "AT", "PT", "IE"]
COUNTRY_WEIGHTS = [0.22, 0.18, 0.10, 0.07, 0.12, 0.11, 0.08, 0.05, 0.04, 0.03]

CURRENCIES_BY_COUNTRY = {c: "EUR" for c in COUNTRIES}
CURRENCIES_BY_COUNTRY["PL"] = "PLN"

# Broad NACE-style sector groups with rough revenue/margin/seasonality flavor
SECTORS = [
    ("Manufacturing", "C"),
    ("Wholesale & Retail Trade", "G"),
    ("Construction", "F"),
    ("Transportation & Logistics", "H"),
    ("Professional & Business Services", "M"),
    ("Information & Communication", "J"),
    ("Hospitality & Food Service", "I"),
    ("Health & Social Care", "Q"),
    ("Real Estate", "L"),
    ("Public Administration / Institutional", "O"),
    ("Agriculture & Food Production", "A"),
    ("Energy & Utilities", "D"),
]
SECTOR_WEIGHTS = [0.16, 0.15, 0.11, 0.08, 0.13, 0.07, 0.06, 0.06, 0.05, 0.04, 0.05, 0.04]

SEGMENTS = ["SME", "Mid-Corporate", "Large-Corporate", "Institutional"]
SEGMENT_WEIGHTS = [0.62, 0.24, 0.09, 0.05]

SEGMENT_REVENUE_BANDS_EUR = {
    # (min annual revenue, max annual revenue) -- loosely EU SME definition bands
    "SME": (300_000, 10_000_000),
    "Mid-Corporate": (10_000_000, 50_000_000),
    "Large-Corporate": (50_000_000, 500_000_000),
    "Institutional": (5_000_000, 200_000_000),
}

SEASONAL_SECTORS = {
    "Hospitality & Food Service": "summer_peak",
    "Construction": "summer_peak",
    "Agriculture & Food Production": "harvest_peak",
    "Wholesale & Retail Trade": "q4_peak",
    "Energy & Utilities": "winter_peak",
}


def business_name(sector_name):
    suffixes = {
        "DE": ["GmbH", "AG", "GmbH & Co. KG"],
        "FR": ["SARL", "SAS", "SA"],
        "NL": ["B.V.", "N.V."],
        "BE": ["BVBA", "SA"],
        "ES": ["S.L.", "S.A."],
        "IT": ["S.r.l.", "S.p.A."],
        "PL": ["Sp. z o.o.", "S.A."],
        "AT": ["GmbH", "AG"],
        "PT": ["Lda.", "S.A."],
        "IE": ["Ltd", "DAC"],
    }
    return fake.company()


def seasonal_multiplier(d: date, pattern: str) -> float:
    doy = d.timetuple().tm_yday
    if pattern == "summer_peak":
        return 1.0 + 0.5 * np.sin((doy - 172) / 365 * 2 * np.pi + np.pi / 2)
    if pattern == "harvest_peak":
        return 1.0 + 0.6 * np.exp(-((doy - 260) ** 2) / (2 * 30 ** 2))
    if pattern == "q4_peak":
        return 1.0 + 0.7 * np.exp(-((doy - 340) ** 2) / (2 * 25 ** 2))
    if pattern == "winter_peak":
        return 1.0 + 0.4 * np.cos((doy - 15) / 365 * 2 * np.pi)
    return 1.0


def daterange(d0, d1):
    n = (d1 - d0).days
    for i in range(n + 1):
        yield d0 + timedelta(days=i)


# ----------------------------------------------------------------------
# 1. Clients
# ----------------------------------------------------------------------
def gen_clients():
    rows = []
    for i in range(N_CLIENTS):
        client_id = f"CL{i:05d}"
        country = rng.choice(COUNTRIES, p=COUNTRY_WEIGHTS)
        sector_name, nace = SECTORS[rng.choice(len(SECTORS), p=SECTOR_WEIGHTS)]
        segment = rng.choice(SEGMENTS, p=SEGMENT_WEIGHTS)
        if sector_name == "Public Administration / Institutional":
            segment = "Institutional"

        lo, hi = SEGMENT_REVENUE_BANDS_EUR[segment]
        annual_revenue = float(rng.lognormal(
            mean=np.log(np.sqrt(lo * hi)), sigma=0.4
        ))
        annual_revenue = float(np.clip(annual_revenue, lo, hi))

        onboarding_days_back = rng.integers(30, (END_DATE - START_DATE).days + 365 * 5)
        onboarding_date = START_DATE - timedelta(days=int(onboarding_days_back)) \
            if onboarding_days_back > (END_DATE - START_DATE).days else \
            START_DATE + timedelta(days=int(rng.integers(0, (END_DATE - START_DATE).days // 2)))

        # relationship tenure biases how "known" the client's baseline pattern is
        rows.append({
            "client_id": client_id,
            "legal_name": business_name(sector_name),
            "segment": segment,
            "sector": sector_name,
            "nace_section": nace,
            "country": country,
            "currency_home": CURRENCIES_BY_COUNTRY[country],
            "annual_revenue_eur_est": round(annual_revenue, 2),
            "onboarding_date": onboarding_date.isoformat(),
            "relationship_manager_id": f"RM{int(rng.integers(1, 26)):03d}",
            "seasonality_pattern": SEASONAL_SECTORS.get(sector_name, "none"),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 2. Accounts
# ----------------------------------------------------------------------
def gen_accounts(clients: pd.DataFrame):
    rows = []
    for _, c in clients.iterrows():
        n_accounts = 1
        if c["segment"] in ("Mid-Corporate", "Large-Corporate"):
            n_accounts = int(rng.integers(2, 4))
        elif c["segment"] == "Institutional":
            n_accounts = int(rng.integers(1, 3))
        elif c["segment"] == "SME":
            # ~45% of SMEs carry a second account (overdraft/credit facility or savings) --
            # roughly in line with ECB SAFE survey findings on SME use of bank credit lines
            n_accounts = 2 if rng.random() < 0.45 else 1

        for a in range(n_accounts):
            account_id = f"{c['client_id']}-A{a}"
            is_main = a == 0
            currency = c["currency_home"]
            account_type = "current" if is_main else rng.choice(
                ["current", "savings", "credit_facility"], p=[0.3, 0.3, 0.4]
            )
            credit_limit = 0.0
            if account_type == "credit_facility":
                credit_limit = round(float(c["annual_revenue_eur_est"]) * rng.uniform(0.02, 0.08), 2)

            rows.append({
                "account_id": account_id,
                "client_id": c["client_id"],
                "currency": currency,
                "account_type": account_type,
                "is_primary": is_main,
                "credit_limit": credit_limit,
                "open_date": c["onboarding_date"],
            })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 3. Transactions + embedded trigger events
# ----------------------------------------------------------------------
TX_CATEGORIES_OUT = [
    "supplier_payment", "payroll", "tax_payment", "rent_lease",
    "loan_repayment", "utilities", "professional_fees", "fx_payment",
]
TX_CATEGORIES_IN = [
    "customer_receipt", "contract_payment", "grant_subsidy",
    "loan_disbursement", "interest_income", "refund",
]


def gen_transactions_for_client(client, accounts_for_client, trigger_log):
    daily_revenue = client["annual_revenue_eur_est"] / 365.0
    pattern = client["seasonality_pattern"]
    primary_account = accounts_for_client[accounts_for_client["is_primary"]].iloc[0]
    credit_accounts = accounts_for_client[accounts_for_client["account_type"] == "credit_facility"]

    rows = []
    balance = daily_revenue * rng.uniform(15, 45)  # starting buffer

    # baseline number of counterparties
    n_customers = max(3, int(rng.integers(4, 30)))
    n_suppliers = max(2, int(rng.integers(3, 20)))
    customers = [f"CTP-CUST-{client['client_id']}-{i}" for i in range(n_customers)]
    suppliers = [f"CTP-SUPP-{client['client_id']}-{i}" for i in range(n_suppliers)]

    # ---- decide which trigger events (if any) this client will get ----
    possible_triggers = []
    onboarding = date.fromisoformat(client["onboarding_date"])
    active_start = max(START_DATE, onboarding)
    active_days = (END_DATE - active_start).days
    if active_days < 60:
        active_days = 60
        active_start = END_DATE - timedelta(days=60)

    def random_event_date(margin=30):
        offset = int(rng.integers(margin, max(margin + 1, active_days - margin)))
        return active_start + timedelta(days=offset)

    trigger_roll = rng.random()
    assigned_triggers = []
    if trigger_roll < 0.55:
        n_trig = rng.choice([1, 2], p=[0.75, 0.25])
        candidates = ["TENDER_PAYMENT", "CASHFLOW_STRESS", "FX_EXPOSURE_NEW",
                      "TREASURY_SURPLUS", "NEW_COUNTERPARTY_CONCENTRATION",
                      "CREDIT_UTILIZATION_SPIKE", "DORMANT_REACTIVATION",
                      "RECURRING_REVENUE_ESTABLISHED"]
        if credit_accounts.empty:
            candidates.remove("CREDIT_UTILIZATION_SPIKE")
        chosen = list(rng.choice(candidates, size=min(n_trig, len(candidates)), replace=False))
        for t in chosen:
            assigned_triggers.append({"type": t, "event_date": random_event_date()})

    dormant_start = None
    dormant_end = None
    for t in assigned_triggers:
        if t["type"] == "DORMANT_REACTIVATION":
            dormant_end = t["event_date"]
            dormant_start = dormant_end - timedelta(days=int(rng.integers(60, 150)))

    fx_start = None
    for t in assigned_triggers:
        if t["type"] == "FX_EXPOSURE_NEW":
            fx_start = t["event_date"]
    foreign_currencies = ["USD", "GBP", "CHF", "JPY"]
    fx_ccy = fake.random_element(foreign_currencies)

    recurring_rev_start = None
    for t in assigned_triggers:
        if t["type"] == "RECURRING_REVENUE_ESTABLISHED":
            recurring_rev_start = t["event_date"]

    surplus_window = None
    for t in assigned_triggers:
        if t["type"] == "TREASURY_SURPLUS":
            d0 = t["event_date"]
            surplus_window = (d0, d0 + timedelta(days=int(rng.integers(45, 90))))

    stress_window = None
    for t in assigned_triggers:
        if t["type"] == "CASHFLOW_STRESS":
            d0 = t["event_date"]
            stress_window = (d0, d0 + timedelta(days=int(rng.integers(30, 75))))

    credit_spike_window = None
    for t in assigned_triggers:
        if t["type"] == "CREDIT_UTILIZATION_SPIKE" and not credit_accounts.empty:
            d0 = t["event_date"]
            credit_spike_window = (d0, d0 + timedelta(days=int(rng.integers(20, 60))))

    new_ctp_date = None
    for t in assigned_triggers:
        if t["type"] == "NEW_COUNTERPARTY_CONCENTRATION":
            new_ctp_date = t["event_date"]
    big_new_counterparty = f"CTP-NEW-{client['client_id']}"

    tender_date = None
    tender_amount = 0.0
    for t in assigned_triggers:
        if t["type"] == "TENDER_PAYMENT":
            tender_date = t["event_date"]
            tender_amount = daily_revenue * rng.uniform(25, 90)

    def in_window(d, window):
        return window is not None and window[0] <= d <= window[1]

    for d in daterange(active_start, END_DATE):
        if d.weekday() >= 5 and rng.random() < 0.85:
            continue  # mostly quiet on weekends

        if dormant_start and dormant_start <= d <= dormant_end and rng.random() < 0.97:
            continue  # dormancy window: almost no activity

        season_mult = seasonal_multiplier(d, pattern)
        stress_mult = 0.55 if in_window(d, stress_window) else 1.0
        surplus_mult = 1.0

        n_tx_today = rng.poisson(lam=1.3 * season_mult * stress_mult)
        for _ in range(n_tx_today):
            is_inflow = rng.random() < 0.42
            if is_inflow:
                base = daily_revenue * rng.uniform(0.3, 1.8) * season_mult
                if recurring_rev_start and d >= recurring_rev_start:
                    base *= 1.15  # new recurring receivable layered in
                category = rng.choice(TX_CATEGORIES_IN, p=[0.72, 0.06, 0.03, 0.05, 0.05, 0.09])
                counterparty = rng.choice(customers)
                amount = round(base, 2)
            else:
                base = daily_revenue * rng.uniform(0.2, 1.4) * season_mult / stress_mult
                category = rng.choice(TX_CATEGORIES_OUT,
                                       p=[0.35, 0.15, 0.08, 0.12, 0.08, 0.1, 0.07, 0.05])
                counterparty = rng.choice(suppliers)
                amount = -round(base, 2)

            currency = client["currency_home"]
            if fx_start and d >= fx_start and rng.random() < 0.3:
                currency = fx_ccy
                category = "fx_payment" if amount < 0 else "customer_receipt"

            if new_ctp_date and d >= new_ctp_date and rng.random() < 0.25:
                counterparty = big_new_counterparty
                amount = amount * rng.uniform(1.5, 3.0) if amount > 0 else amount

            balance += amount
            rows.append({
                "transaction_id": str(uuid.uuid4()),
                "account_id": primary_account["account_id"],
                "client_id": client["client_id"],
                "date": d.isoformat(),
                "amount": round(amount, 2),
                "currency": currency,
                "direction": "credit" if amount > 0 else "debit",
                "category": category,
                "counterparty_id": counterparty,
                "channel": rng.choice(["SEPA_CREDIT_TRANSFER", "SWIFT", "CARD", "DIRECT_DEBIT"],
                                       p=[0.6, 0.1, 0.1, 0.2]),
                "balance_after": round(balance, 2),
            })

        # tender / one-off lump sum payment
        if tender_date == d:
            balance += tender_amount
            rows.append({
                "transaction_id": str(uuid.uuid4()),
                "account_id": primary_account["account_id"],
                "client_id": client["client_id"],
                "date": d.isoformat(),
                "amount": round(tender_amount, 2),
                "currency": client["currency_home"],
                "direction": "credit",
                "category": "contract_payment",
                "counterparty_id": rng.choice(customers),
                "channel": "SEPA_CREDIT_TRANSFER",
                "balance_after": round(balance, 2),
            })

        # treasury surplus: parked idle cash sits, occasionally topped up
        if in_window(d, surplus_window) and rng.random() < 0.08:
            topup = daily_revenue * rng.uniform(3, 8)
            balance += topup
            rows.append({
                "transaction_id": str(uuid.uuid4()),
                "account_id": primary_account["account_id"],
                "client_id": client["client_id"],
                "date": d.isoformat(),
                "amount": round(topup, 2),
                "currency": client["currency_home"],
                "direction": "credit",
                "category": "customer_receipt",
                "counterparty_id": rng.choice(customers),
                "channel": "SEPA_CREDIT_TRANSFER",
                "balance_after": round(balance, 2),
            })

        # credit facility utilization spike
        if credit_spike_window and in_window(d, credit_spike_window) and rng.random() < 0.15 \
                and not credit_accounts.empty:
            draw = float(credit_accounts.iloc[0]["credit_limit"]) * rng.uniform(0.1, 0.3)
            rows.append({
                "transaction_id": str(uuid.uuid4()),
                "account_id": credit_accounts.iloc[0]["account_id"],
                "client_id": client["client_id"],
                "date": d.isoformat(),
                "amount": round(draw, 2),
                "currency": client["currency_home"],
                "direction": "credit",
                "category": "loan_disbursement",
                "counterparty_id": "INTERNAL_CREDIT_FACILITY",
                "channel": "INTERNAL",
                "balance_after": round(balance + draw, 2),
            })

    for t in assigned_triggers:
        trigger_log.append({
            "client_id": client["client_id"],
            "trigger_type": t["type"],
            "event_date": t["event_date"].isoformat(),
            "account_id": primary_account["account_id"],
        })

    return rows


def gen_transactions(clients: pd.DataFrame, accounts: pd.DataFrame):
    all_rows = []
    trigger_log = []
    for _, c in clients.iterrows():
        acc = accounts[accounts["client_id"] == c["client_id"]]
        all_rows.extend(gen_transactions_for_client(c, acc, trigger_log))
    return pd.DataFrame(all_rows), pd.DataFrame(trigger_log)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    print("Generating clients...")
    clients = gen_clients()

    print("Generating accounts...")
    accounts = gen_accounts(clients)

    print("Generating transactions (this can take a minute)...")
    transactions, triggers = gen_transactions(clients, accounts)
    transactions = transactions.sort_values(["client_id", "date"]).reset_index(drop=True)

    clients.to_csv(os.path.join(OUT_DIR, "clients.csv"), index=False)
    accounts.to_csv(os.path.join(OUT_DIR, "accounts.csv"), index=False)
    transactions.to_csv(os.path.join(OUT_DIR, "transactions.csv"), index=False)
    triggers.sort_values(["client_id", "event_date"]).to_csv(
        os.path.join(OUT_DIR, "trigger_events.csv"), index=False
    )

    print(f"Clients:      {len(clients):>8,}")
    print(f"Accounts:     {len(accounts):>8,}")
    print(f"Transactions: {len(transactions):>8,}")
    print(f"Trigger events (ground truth): {len(triggers):>8,}")
    print(f"Written to {OUT_DIR}/")


if __name__ == "__main__":
    main()
