"""
Synthetic commercial/institutional banking dataset generator for a
Next-Best-Action (NBA) / Event-Based-Marketing (EBM) proof of concept.

This does NOT use any real bank data, and does not reproduce raw records
from any dataset below. The RELATIONAL SCHEMA and the FORMAT of certain
fields are deliberately modeled on real, publicly documented references so
the structure resembles what a commercial/institutional banking analytics
division actually works with, rather than a flat, ad hoc table:

  - Berka / PKDD'99 "Financial Dataset" (Discovery Challenge, 1999) -- real,
    anonymized Czech retail-bank data, still used across university data
    mining courses and the CTU Prague Relational Learning Repository. Its
    normalized structure (client / account / disposition / transaction /
    loan as separate entities, not one flat table) is the backbone for the
    clients / accounts / facilities / transactions split here.
  - Lending Club public loan-level dataset -- widely used in academic
    credit-risk/ML research; informs facilities.csv (grade-like rating,
    term, purpose, status) instead of ad hoc "credit draw" rows.
  - UCI Statlog (German Credit Data) -- canonical academic credit-risk
    dataset; informs risk_ratings.csv's masterscale structure.
  - PaySim (Lopez-Rojas et al., 2016) -- synthetic mobile-money transaction
    methodology widely used in academic fraud-detection research; informs
    the balance-before/after and counterparty-token conventions.
  - ISO 20022 External Purpose Code list and the real booking-date vs.
    value-date convention -- the actual global standard bank payments data
    engineers key transactions off (not academic, but what real analytics
    divisions consume from core banking / payment hubs).
  - IBAN (ISO 7064 MOD 97-10 checksum) and LEI (ISO 17442) -- real public
    identifier standards. IBANs/LEIs here are structurally valid (correct
    length, correct check digits) but are NOT registered real identifiers.
  - Eurostat NACE Rev. 2 -- real EU industry classification; sectors are
    tagged with real 4-digit codes instead of a single broad letter.

Distributional choices (revenue bands by segment, seasonality, sector mix,
SME credit-line prevalence, group-structure prevalence) are loosely
calibrated to public aggregate statistics (ECB SAFE survey on SME access to
finance, Eurostat structural business statistics, ECB payments statistics)
-- not fitted to any real client-level data, which does not exist for us to
see.

Output: CSV files in ./output/
    entity_groups.csv    corporate/institutional group hierarchy (2-tier)
    clients.csv          legal entities (may belong to a group), incl.
                          lightweight PEP/sanctions-screening fields
    accounts.csv         bank accounts (IBAN/BIC, per client), incl. closures
    facilities.csv       product holdings: loans, credit lines, trade finance
    risk_ratings.csv      annual internal credit rating per client
    transactions.csv      payment-level transaction feed, incl. bank_fee
                           rows, ISO 20022/legacy message-type tagging, and
                           counterparty jurisdiction risk tagging
    balances.csv           end-of-day balance snapshots (primary accounts)
    crm_interactions.csv  RM/campaign engagement log (source of ML labels)
    trigger_events.csv    ground truth trigger labels (evaluation only)
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
# trigger_events.csv is ground truth for evaluating a detector, not an input
# to it. It lives in a separate path so detector code and coding-assistant
# sessions don't casually read it alongside the real source tables. This is
# a convenience boundary, not an access-control guarantee -- see the note
# left in this directory.
PROTECTED_DIR = os.path.join(OUT_DIR, "protected_evaluator_only")

rng = np.random.default_rng(SEED)
fake = Faker(["en_GB", "de_DE", "fr_FR", "nl_NL", "es_ES", "it_IT"])
Faker.seed(SEED)

COUNTRIES = ["DE", "FR", "NL", "BE", "ES", "IT", "PL", "AT", "PT", "IE"]
COUNTRY_WEIGHTS = [0.22, 0.18, 0.10, 0.07, 0.12, 0.11, 0.08, 0.05, 0.04, 0.03]

CURRENCIES_BY_COUNTRY = {c: "EUR" for c in COUNTRIES}
CURRENCIES_BY_COUNTRY["PL"] = "PLN"

# sector name -> (NACE Rev.2 section letter, [plausible real 4-digit NACE codes])
SECTORS = {
    "Manufacturing": ("C", ["10.71", "13.20", "22.29", "25.11", "28.99"]),
    "Wholesale & Retail Trade": ("G", ["45.20", "46.39", "46.90", "47.11"]),
    "Construction": ("F", ["41.20", "42.11", "43.29"]),
    "Transportation & Logistics": ("H", ["49.41", "52.10", "53.20"]),
    "Professional & Business Services": ("M", ["69.20", "70.22", "71.12"]),
    "Information & Communication": ("J", ["61.10", "62.01", "63.11"]),
    "Hospitality & Food Service": ("I", ["55.10", "56.10"]),
    "Health & Social Care": ("Q", ["86.10", "87.30"]),
    "Real Estate": ("L", ["68.20", "68.31"]),
    "Public Administration / Institutional": ("O", ["84.11", "84.30"]),
    "Agriculture & Food Production": ("A", ["01.11", "10.51"]),
    "Energy & Utilities": ("D", ["35.11", "35.30"]),
}
SECTOR_NAMES = list(SECTORS.keys())
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

# fictitious bank brands (NOT real bank BIC codes) used to build structurally
# valid-looking BICs, e.g. "NOVADEXX"
FICTITIOUS_BANK_CODES = ["NOVA", "AURA", "VELO", "AXIO", "UNIO",
                          "PRIMA", "NORD", "SOLI", "METR", "CIVI"]

# real ISO 20022 External Purpose Code values, mapped from our transaction
# categories (https://www.iso20022.org/catalogue-messages/additional-content-issue-type/external-code-sets)
ISO20022_PURPOSE_CODE = {
    "supplier_payment": "SUPP",
    "payroll": "SALA",
    "tax_payment": "TAXS",
    "rent_lease": "RENT",
    "loan_repayment": "LOAN",
    "utilities": "UBIL",
    "professional_fees": "SCVE",
    "fx_payment": "FREX",
    "customer_receipt": "GDDS",
    "contract_payment": "TRAD",
    "grant_subsidy": "GOVT",
    "loan_disbursement": "LOAN",
    "interest_income": "INTE",
    "refund": "RREF",
}

# real value-date lag conventions by payment channel (settlement, not booking)
VALUE_DATE_LAG_DAYS = {
    "SEPA_CREDIT_TRANSFER": 1,
    "SWIFT": 2,
    "CARD": 0,
    "DIRECT_DEBIT": 1,
    "INTERNAL": 0,
}

# message-format tagging by channel. SEPA/direct debit map to real ISO 20022
# message types; cross-border SWIFT wires are tagged with the legacy MT103
# format still common on many corridors pre-CBPR+ migration; card rails use
# ISO 8583, a different standard entirely, not ISO 20022; internal book
# transfers have no external wire format.
MESSAGE_TYPE_BY_CHANNEL = {
    "SEPA_CREDIT_TRANSFER": "pacs.008",
    "SWIFT": "MT103",
    "DIRECT_DEBIT": "pacs.003",
    "CARD": "ISO8583",
    "INTERNAL": "internal_gl_posting",
}

# fictitious jurisdiction code (never a real country) representing an
# elevated-monitoring counterparty jurisdiction, so the schema carries a
# real AML-style risk tag without asserting real-world sanctions status
# about any actual country
ELEVATED_MONITORING_JURISDICTION = "XZ"


def _iso7064_check_digits(alnum: str) -> str:
    """ISO 7064 MOD 97-10 check digits, used by both IBAN and LEI."""
    numeric = "".join(str(int(ch, 36)) for ch in alnum)
    remainder = int(numeric) % 97
    return f"{98 - remainder:02d}"


def gen_iban(country: str) -> str:
    """Structurally valid IBAN (correct length + real MOD 97-10 checksum),
    not a registered real account number."""
    bban_len = {"DE": 18, "FR": 23, "NL": 14, "BE": 12, "ES": 20,
                "IT": 23, "PL": 24, "AT": 16, "PT": 21, "IE": 18}[country]
    if country in ("NL", "IE"):
        letters = "".join(rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")) for _ in range(4))
        digits = "".join(str(int(rng.integers(0, 10))) for _ in range(bban_len - 4))
        bban = letters + digits
    elif country == "IT":
        letter = rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        digits = "".join(str(int(rng.integers(0, 10))) for _ in range(bban_len - 1))
        bban = letter + digits
    else:
        bban = "".join(str(int(rng.integers(0, 10))) for _ in range(bban_len))
    check = _iso7064_check_digits(bban + country + "00")
    return f"{country}{check}{bban}"


def gen_bic(country: str) -> str:
    brand = rng.choice(FICTITIOUS_BANK_CODES)
    location = "".join(rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")) for _ in range(2))
    return f"{brand}{country}{location}"


def gen_lei() -> str:
    """Structurally valid LEI (correct length + real MOD 97-10 checksum
    per ISO 17442), not a GLEIF-registered real identifier."""
    lou_prefix = rng.choice(["5299", "7245", "2138", "5493", "3157", "8945"])
    alnum = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    entity_part = "".join(rng.choice(list(alnum)) for _ in range(14))
    base18 = lou_prefix + entity_part
    check = _iso7064_check_digits(base18 + "00")
    return base18 + check


def business_name(sector_name):
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


def add_business_days(d: date, n: int) -> date:
    cur = d
    while n > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur


# ----------------------------------------------------------------------
# 1. Entity groups (corporate/institutional hierarchy) -- most real
#    corporate/institutional NBA decisions are made at group, not single
#    legal-entity, level.
# ----------------------------------------------------------------------
GROUP_MEMBERSHIP_PROB = {
    "SME": 0.10, "Mid-Corporate": 0.25, "Large-Corporate": 0.55, "Institutional": 0.05,
}


def assign_groups(clients: pd.DataFrame):
    pool = []
    for _, c in clients.iterrows():
        if rng.random() < GROUP_MEMBERSHIP_PROB[c["segment"]]:
            pool.append(c["client_id"])
    rng.shuffle(np.array(pool, dtype=object)) if pool else None
    pool = list(rng.permutation(pool)) if pool else []

    groups = []
    # client_id -> (group_id, role, ownership_pct, parent_client_id)
    # parent_client_id is the IMMEDIATE parent within the group -- distinct
    # from ultimate_parent_client_id on entity_groups.csv -- so groups with
    # enough members form a real two-tier tree (ultimate parent ->
    # intermediate holding -> subsidiary), not just a flat parent+subs list.
    membership = {}
    i = 0
    gnum = 0
    while i < len(pool):
        size = int(rng.integers(2, 5))
        chunk = pool[i:i + size]
        i += size
        if len(chunk) < 2:
            break
        gnum += 1
        group_id = f"GRP{gnum:04d}"
        parent_id = chunk[0]
        parent_row = clients[clients["client_id"] == parent_id].iloc[0]
        group_name = f"{parent_row['legal_name'].split(',')[0]} Group"
        group_type = "institutional_consolidation" if parent_row["segment"] == "Institutional" \
            else "corporate_group"
        groups.append({
            "group_id": group_id,
            "group_name": group_name,
            "ultimate_parent_client_id": parent_id,
            "ultimate_parent_country": parent_row["country"],
            "group_type": group_type,
            "member_count": len(chunk),
        })
        membership[parent_id] = (group_id, "ultimate_parent", 100.0, None)
        subs = chunk[1:]
        if len(subs) >= 3:
            intermediate_id = subs[0]
            membership[intermediate_id] = (
                group_id, "intermediate_holding", round(float(rng.uniform(70, 100)), 1), parent_id
            )
            for sub in subs[1:]:
                reports_to = intermediate_id if rng.random() < 0.6 else parent_id
                membership[sub] = (
                    group_id, "subsidiary", round(float(rng.uniform(51, 100)), 1), reports_to
                )
        else:
            for sub in subs:
                membership[sub] = (
                    group_id, "subsidiary", round(float(rng.uniform(51, 100)), 1), parent_id
                )
    return pd.DataFrame(groups), membership


# ----------------------------------------------------------------------
# 2. Clients
# ----------------------------------------------------------------------
def gen_clients():
    rows = []
    for i in range(N_CLIENTS):
        client_id = f"CL{i:05d}"
        country = rng.choice(COUNTRIES, p=COUNTRY_WEIGHTS)
        sector_idx = rng.choice(len(SECTOR_NAMES), p=SECTOR_WEIGHTS)
        sector_name = SECTOR_NAMES[sector_idx]
        nace_section, nace_codes = SECTORS[sector_name]
        nace_4digit = rng.choice(nace_codes)
        segment = rng.choice(SEGMENTS, p=SEGMENT_WEIGHTS)
        if sector_name == "Public Administration / Institutional":
            segment = "Institutional"

        lo, hi = SEGMENT_REVENUE_BANDS_EUR[segment]
        annual_revenue = float(rng.lognormal(mean=np.log(np.sqrt(lo * hi)), sigma=0.4))
        annual_revenue = float(np.clip(annual_revenue, lo, hi))

        onboarding_days_back = rng.integers(30, (END_DATE - START_DATE).days + 365 * 5)
        onboarding_date = START_DATE - timedelta(days=int(onboarding_days_back)) \
            if onboarding_days_back > (END_DATE - START_DATE).days else \
            START_DATE + timedelta(days=int(rng.integers(0, (END_DATE - START_DATE).days // 2)))

        # LEI adoption in reality skews heavily to larger/regulated entities
        # (EMIR/MiFID II-scoped) -- most SMEs never obtain one
        lei_prob = {"SME": 0.15, "Mid-Corporate": 0.55, "Large-Corporate": 0.95,
                    "Institutional": 0.85}[segment]
        has_lei = rng.random() < lei_prob

        # lightweight AML/KYC screening fields -- real banks periodically
        # re-screen every client against PEP and sanctions lists; pep_flag
        # skews higher for Institutional/public-sector entities (public
        # officials sit on their boards more often than in private SMEs).
        # sanctions_screening_status only ever reflects a past false-positive
        # that was cleared -- an *active* dataset client is never modeled as
        # currently sanctioned.
        pep_prob = 0.06 if segment == "Institutional" else 0.02
        pep_flag = rng.random() < pep_prob
        sanctions_screening_status = "cleared_after_review" if rng.random() < 0.03 else "clear"
        screening_lookback_days = min(540, max(30, (END_DATE - onboarding_date).days))
        last_screening_date = END_DATE - timedelta(days=int(rng.integers(0, screening_lookback_days)))

        rows.append({
            "client_id": client_id,
            "legal_name": business_name(sector_name),
            "lei": gen_lei() if has_lei else "",
            "segment": segment,
            "sector": sector_name,
            "nace_section": nace_section,
            "nace_code": nace_4digit,
            "country": country,
            "currency_home": CURRENCIES_BY_COUNTRY[country],
            "annual_revenue_eur_est": round(annual_revenue, 2),
            "onboarding_date": onboarding_date.isoformat(),
            "relationship_manager_id": f"RM{int(rng.integers(1, 26)):03d}",
            "seasonality_pattern": SEASONAL_SECTORS.get(sector_name, "none"),
            "pep_flag": pep_flag,
            "sanctions_screening_status": sanctions_screening_status,
            "last_screening_date": last_screening_date.isoformat(),
        })
    clients = pd.DataFrame(rows)

    groups_df, membership = assign_groups(clients)
    clients["group_id"] = clients["client_id"].map(lambda cid: membership.get(cid, (None, None, None, None))[0])
    clients["group_role"] = clients["client_id"].map(
        lambda cid: membership.get(cid, (None, "standalone", None, None))[1])
    clients["group_ownership_pct"] = clients["client_id"].map(
        lambda cid: membership.get(cid, (None, None, None, None))[2])
    clients["parent_client_id"] = clients["client_id"].map(
        lambda cid: membership.get(cid, (None, None, None, None))[3])
    return clients, groups_df


# ----------------------------------------------------------------------
# 3. Accounts
# ----------------------------------------------------------------------
# probability a client is GUARANTEED at least one credit_facility account,
# rather than leaving eligibility to a random 3-way account-type draw --
# fixes CREDIT_UTILIZATION_SPIKE trigger candidates being underrepresented
# among smaller segments. Roughly in line with ECB SAFE survey findings on
# SME/corporate use of bank credit lines.
CREDIT_FACILITY_PROB = {"SME": 0.45, "Mid-Corporate": 0.70, "Large-Corporate": 0.85, "Institutional": 0.55}


def gen_accounts(clients: pd.DataFrame):
    rows = []
    for _, c in clients.iterrows():
        n_accounts = 1
        if c["segment"] in ("Mid-Corporate", "Large-Corporate"):
            n_accounts = int(rng.integers(2, 4))
        elif c["segment"] == "Institutional":
            n_accounts = int(rng.integers(1, 3))
        elif c["segment"] == "SME":
            n_accounts = 2 if rng.random() < 0.45 else 1

        gets_credit_facility = rng.random() < CREDIT_FACILITY_PROB[c["segment"]]
        if gets_credit_facility and n_accounts < 2:
            n_accounts = 2

        # multi-banking: larger clients often hold accounts at more than one
        # bank brand -- a real, common corporate-treasury behavior
        n_banks = 1
        if c["segment"] in ("Large-Corporate", "Institutional"):
            n_banks = int(rng.integers(1, 3))
        client_banks = [gen_bic(c["country"]) for _ in range(n_banks)]

        onboarding = date.fromisoformat(c["onboarding_date"])
        credit_facility_assigned = False
        for a in range(n_accounts):
            account_id = f"{c['client_id']}-A{a}"
            is_main = a == 0
            currency = c["currency_home"]
            if is_main:
                account_type = "current"
            elif gets_credit_facility and not credit_facility_assigned:
                account_type = "credit_facility"
                credit_facility_assigned = True
            else:
                account_type = rng.choice(["current", "savings"], p=[0.45, 0.55])

            credit_limit = 0.0
            if account_type == "credit_facility":
                credit_limit = round(float(c["annual_revenue_eur_est"]) * rng.uniform(0.02, 0.08), 2)

            # secondary accounts occasionally close before the end of the
            # observation window -- real accounts don't stay open forever
            account_status = "active"
            close_date = ""
            if not is_main and rng.random() < 0.08:
                open_anchor = max(onboarding, START_DATE)
                span_days = (END_DATE - open_anchor).days
                if span_days > 120:
                    close_offset = int(rng.integers(90, span_days))
                    account_status = "closed"
                    close_date = (open_anchor + timedelta(days=close_offset)).isoformat()

            rows.append({
                "account_id": account_id,
                "client_id": c["client_id"],
                "iban": gen_iban(c["country"]),
                "bic": client_banks[a % len(client_banks)],
                "currency": currency,
                "account_type": account_type,
                "is_primary": is_main,
                "account_status": account_status,
                "close_date": close_date,
                "credit_limit": credit_limit,
                "open_date": c["onboarding_date"],
            })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 4. Facilities (product holdings) -- modeled on public loan-level datasets
#    (e.g. Lending Club: amount, term, rate, grade-like status, purpose)
#    adapted to corporate/institutional products.
# ----------------------------------------------------------------------
FACILITY_TYPES_BY_SEGMENT = {
    "SME": ["overdraft", "term_loan"],
    "Mid-Corporate": ["term_loan", "revolving_credit_facility", "trade_finance_lc", "bank_guarantee"],
    "Large-Corporate": ["term_loan", "revolving_credit_facility", "trade_finance_lc",
                         "bank_guarantee", "fx_forward_line", "syndicated_term_loan"],
    "Institutional": ["term_loan", "revolving_credit_facility", "bank_guarantee"],
}
FACILITY_PURPOSES = ["working_capital", "equipment_finance", "real_estate",
                      "trade_finance", "general_corporate", "refinancing"]
N_FACILITIES_LAMBDA = {"SME": 0.8, "Mid-Corporate": 1.6, "Large-Corporate": 2.8, "Institutional": 1.4}


def gen_facilities(clients: pd.DataFrame, accounts: pd.DataFrame):
    rows = []
    fnum = 0
    for _, c in clients.iterrows():
        client_accounts = accounts[accounts["client_id"] == c["client_id"]]
        credit_accounts = client_accounts[client_accounts["account_type"] == "credit_facility"]
        credit_acc_ptr = 0
        n_fac = int(rng.poisson(N_FACILITIES_LAMBDA[c["segment"]]))
        types = FACILITY_TYPES_BY_SEGMENT[c["segment"]]
        onboarding = date.fromisoformat(c["onboarding_date"])

        for _ in range(n_fac):
            fnum += 1
            facility_id = f"FAC{fnum:06d}"
            ftype = rng.choice(types)
            linked_account = ""
            linked_account_status = "active"
            # round-robin through every credit_facility account the client
            # holds, instead of only ever linking the first one, so a
            # multi-banked/multi-account client's facilities table actually
            # reflects all of its drawable accounts
            if ftype in ("overdraft", "revolving_credit_facility") and not credit_accounts.empty \
                    and credit_acc_ptr < len(credit_accounts):
                acc_row = credit_accounts.iloc[credit_acc_ptr]
                linked_account = acc_row["account_id"]
                linked_account_status = acc_row["account_status"]
                limit_amount = float(acc_row["credit_limit"])
                credit_acc_ptr += 1
            else:
                limit_amount = round(c["annual_revenue_eur_est"] * rng.uniform(0.03, 0.20), 2)

            origination = max(onboarding, START_DATE - timedelta(days=int(rng.integers(0, 365 * 4))))
            term_years = int(rng.integers(1, 8))
            maturity = date(origination.year + term_years, origination.month, min(origination.day, 28))
            status = "active"
            if maturity < START_DATE:
                status = rng.choice(["matured", "refinanced"])
            elif maturity < END_DATE and rng.random() < 0.1:
                status = "closed"
            if linked_account_status == "closed":
                status = "closed"

            utilization = rng.uniform(0.1, 0.85) if ftype in ("overdraft", "revolving_credit_facility") else 1.0
            outstanding = round(limit_amount * utilization, 2) if status == "active" else 0.0

            rows.append({
                "facility_id": facility_id,
                "client_id": c["client_id"],
                "linked_account_id": linked_account,
                "product_type": ftype,
                "purpose": rng.choice(FACILITY_PURPOSES),
                "currency": c["currency_home"],
                "original_amount": limit_amount,
                "outstanding_balance": outstanding,
                "interest_rate_pct": round(float(rng.uniform(2.5, 8.5)), 2),
                "origination_date": origination.isoformat(),
                "maturity_date": maturity.isoformat(),
                "status": status,
                "collateralized": bool(rng.random() < (0.6 if c["segment"] == "SME" else 0.35)),
            })
    return pd.DataFrame(rows)


def apply_utilization_spikes(facilities: pd.DataFrame, triggers: pd.DataFrame) -> pd.DataFrame:
    """Bump the outstanding balance on the facility linked to a client's
    credit_facility account when that client got a CREDIT_UTILIZATION_SPIKE
    trigger -- ties facilities.csv causally to the embedded trigger instead
    of leaving utilization as an independently random snapshot."""
    spike_clients = set(triggers.loc[triggers["trigger_type"] == "CREDIT_UTILIZATION_SPIKE", "client_id"])
    if not spike_clients:
        return facilities
    mask = (
        facilities["client_id"].isin(spike_clients)
        & facilities["product_type"].isin(["overdraft", "revolving_credit_facility"])
        & (facilities["status"] == "active")
    )
    n = int(mask.sum())
    if n:
        facilities.loc[mask, "outstanding_balance"] = (
            facilities.loc[mask, "original_amount"] * rng.uniform(0.65, 0.95, size=n)
        ).round(2)
    return facilities


# ----------------------------------------------------------------------
# 5. Risk ratings -- internal masterscale, informed by academic credit-risk
#    dataset conventions (UCI German Credit / Lending Club "grade").
#    Grade 1 = best (~investment grade), Grade 10 = default-adjacent.
# ----------------------------------------------------------------------
PD_BY_GRADE = {1: 0.03, 2: 0.06, 3: 0.12, 4: 0.25, 5: 0.55,
               6: 1.2, 7: 2.8, 8: 6.5, 9: 12.0, 10: 22.0}
SEGMENT_BASE_GRADE = {"Institutional": 3, "Large-Corporate": 4, "Mid-Corporate": 5, "SME": 6}


def gen_risk_ratings(clients: pd.DataFrame, trigger_log: list):
    stress_clients = {t["client_id"] for t in trigger_log if t["trigger_type"] == "CASHFLOW_STRESS"}
    stress_dates = {t["client_id"]: date.fromisoformat(t["event_date"])
                    for t in trigger_log if t["trigger_type"] == "CASHFLOW_STRESS"}

    rows = []
    rnum = 0
    for _, c in clients.iterrows():
        onboarding = date.fromisoformat(c["onboarding_date"])
        base_grade = SEGMENT_BASE_GRADE[c["segment"]] + int(rng.integers(-1, 2))
        base_grade = int(np.clip(base_grade, 1, 9))

        review_year = max(onboarding.year, START_DATE.year)
        while review_year <= END_DATE.year:
            rating_date = date(review_year, 3, 31)  # annual review cadence
            if rating_date < onboarding or rating_date > END_DATE:
                review_year += 1
                continue
            grade = base_grade
            if c["client_id"] in stress_clients and rating_date > stress_dates[c["client_id"]]:
                grade = min(10, base_grade + int(rng.integers(1, 3)))
            watchlist = grade >= 8 and rng.random() < 0.4
            rnum += 1
            rows.append({
                "rating_id": f"RTG{rnum:06d}",
                "client_id": c["client_id"],
                "rating_date": rating_date.isoformat(),
                "internal_rating_grade": grade,
                "pd_1y_pct": PD_BY_GRADE[grade],
                "watchlist_flag": watchlist,
            })
            review_year += 1
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 6. Transactions + embedded trigger events
# ----------------------------------------------------------------------
TX_CATEGORIES_OUT = [
    "supplier_payment", "payroll", "tax_payment", "rent_lease",
    "loan_repayment", "utilities", "professional_fees", "fx_payment",
]
TX_CATEGORIES_IN = [
    "customer_receipt", "contract_payment", "grant_subsidy",
    "loan_disbursement", "interest_income", "refund",
]


def remittance_text(category, counterparty):
    templates = {
        "supplier_payment": f"INV-{int(rng.integers(10000,99999))} {counterparty}",
        "payroll": f"Payroll run {rng.integers(1,13):02d}/{rng.integers(2023,2026)}",
        "tax_payment": "Corporate tax settlement",
        "rent_lease": "Lease payment",
        "loan_repayment": "Loan instalment",
        "utilities": "Utility bill settlement",
        "professional_fees": "Professional services fee",
        "fx_payment": "FX settlement",
        "customer_receipt": f"Payment ref {counterparty}",
        "contract_payment": "Contract/tender payment",
        "grant_subsidy": "Public grant disbursement",
        "loan_disbursement": "Facility drawdown",
        "interest_income": "Interest credit",
        "refund": "Refund",
        "bank_fee": "Bank charges",
    }
    return templates.get(category, "")


def assign_counterparty_country(home_country):
    """~78% domestic, ~17% a common real EU/major trade-partner country
    (ordinary cross-border business), ~5% a synthetic elevated-monitoring
    jurisdiction placeholder -- never a real country tagged as risky."""
    r = rng.random()
    if r < 0.78:
        return home_country
    if r < 0.95:
        return rng.choice(["DE", "FR", "NL", "GB", "US", "CN", "IT", "ES"])
    return ELEVATED_MONITORING_JURISDICTION


def gen_transactions_for_client(client, accounts_for_client, trigger_log):
    daily_revenue = client["annual_revenue_eur_est"] / 365.0
    pattern = client["seasonality_pattern"]
    primary_account = accounts_for_client[accounts_for_client["is_primary"]].iloc[0]
    credit_accounts = accounts_for_client[
        (accounts_for_client["account_type"] == "credit_facility")
        & (accounts_for_client["account_status"] == "active")
    ]

    rows = []
    balance = daily_revenue * rng.uniform(15, 45)  # starting buffer

    n_customers = max(3, int(rng.integers(4, 30)))
    n_suppliers = max(2, int(rng.integers(3, 20)))
    customers = [f"CTP-CUST-{client['client_id']}-{i}" for i in range(n_customers)]
    suppliers = [f"CTP-SUPP-{client['client_id']}-{i}" for i in range(n_suppliers)]
    counterparty_country = {
        cp: assign_counterparty_country(client["country"]) for cp in customers + suppliers
    }

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
    counterparty_country[big_new_counterparty] = assign_counterparty_country(client["country"])

    tender_date = None
    tender_amount = 0.0
    for t in assigned_triggers:
        if t["type"] == "TENDER_PAYMENT":
            tender_date = t["event_date"]
            tender_amount = daily_revenue * rng.uniform(25, 90)

    def in_window(d, window):
        return window is not None and window[0] <= d <= window[1]

    def make_row(account_id, d, amount, currency, category, counterparty, channel, bal_after):
        cp_country = counterparty_country.get(counterparty, client["country"])
        return {
            "transaction_id": str(uuid.uuid4()),
            "account_id": account_id,
            "client_id": client["client_id"],
            "booking_date": d.isoformat(),
            "value_date": add_business_days(d, VALUE_DATE_LAG_DAYS.get(channel, 1)).isoformat(),
            "amount": round(amount, 2),
            "currency": currency,
            "direction": "credit" if amount > 0 else "debit",
            "category": category,
            "iso20022_purpose_code": ISO20022_PURPOSE_CODE.get(category, "OTHR"),
            "message_type": MESSAGE_TYPE_BY_CHANNEL.get(channel, "internal_gl_posting"),
            "counterparty_id": counterparty,
            "counterparty_country": cp_country,
            "high_risk_counterparty_flag": cp_country == ELEVATED_MONITORING_JURISDICTION,
            "channel": channel,
            "remittance_info": remittance_text(category, counterparty),
            "balance_after": round(bal_after, 2),
        }

    account_fee = {"SME": (15, 60), "Mid-Corporate": (40, 120),
                    "Large-Corporate": (80, 250), "Institutional": (60, 200)}[client["segment"]]
    last_fee_month = None

    for d in daterange(active_start, END_DATE):
        if d.weekday() >= 5 and rng.random() < 0.85:
            continue

        if dormant_start and dormant_start <= d <= dormant_end and rng.random() < 0.97:
            continue

        # monthly account maintenance fee, charged on the first business day
        # of each calendar month the account is active
        month_key = (d.year, d.month)
        if d.day <= 3 and d.weekday() < 5 and month_key != last_fee_month:
            last_fee_month = month_key
            fee = -round(float(rng.uniform(*account_fee)), 2)
            balance += fee
            rows.append(make_row(primary_account["account_id"], d, fee, client["currency_home"],
                                  "bank_fee", "INTERNAL_BANK_FEE", "INTERNAL", balance))

        season_mult = seasonal_multiplier(d, pattern)
        stress_mult = 0.55 if in_window(d, stress_window) else 1.0

        n_tx_today = rng.poisson(lam=1.3 * season_mult * stress_mult)
        for _ in range(n_tx_today):
            is_inflow = rng.random() < 0.42
            if is_inflow:
                base = daily_revenue * rng.uniform(0.3, 1.8) * season_mult
                if recurring_rev_start and d >= recurring_rev_start:
                    base *= 1.15
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
            is_fx = False
            if fx_start and d >= fx_start and rng.random() < 0.3:
                currency = fx_ccy
                category = "fx_payment" if amount < 0 else "customer_receipt"
                is_fx = True

            if new_ctp_date and d >= new_ctp_date and rng.random() < 0.25:
                counterparty = big_new_counterparty
                amount = amount * rng.uniform(1.5, 3.0) if amount > 0 else amount

            channel = rng.choice(["SEPA_CREDIT_TRANSFER", "SWIFT", "CARD", "DIRECT_DEBIT"],
                                  p=[0.6, 0.1, 0.1, 0.2])
            balance += amount
            rows.append(make_row(primary_account["account_id"], d, amount, currency,
                                  category, counterparty, channel, balance))

            # FX conversion fee -- real banks charge a spread/fee on
            # non-home-currency settlement, separate from the payment itself
            if is_fx and rng.random() < 0.4:
                fee = -round(abs(amount) * rng.uniform(0.004, 0.015), 2)
                balance += fee
                rows.append(make_row(primary_account["account_id"], d, fee, client["currency_home"],
                                      "bank_fee", "INTERNAL_BANK_FEE", "INTERNAL", balance))

        if tender_date == d:
            balance += tender_amount
            rows.append(make_row(primary_account["account_id"], d, tender_amount,
                                  client["currency_home"], "contract_payment",
                                  rng.choice(customers), "SEPA_CREDIT_TRANSFER", balance))

        if in_window(d, surplus_window) and rng.random() < 0.08:
            topup = daily_revenue * rng.uniform(3, 8)
            balance += topup
            rows.append(make_row(primary_account["account_id"], d, topup,
                                  client["currency_home"], "customer_receipt",
                                  rng.choice(customers), "SEPA_CREDIT_TRANSFER", balance))

        if credit_spike_window and in_window(d, credit_spike_window) and rng.random() < 0.15 \
                and not credit_accounts.empty:
            draw = float(credit_accounts.iloc[0]["credit_limit"]) * rng.uniform(0.1, 0.3)
            rows.append(make_row(credit_accounts.iloc[0]["account_id"], d, draw,
                                  client["currency_home"], "loan_disbursement",
                                  "INTERNAL_CREDIT_FACILITY", "INTERNAL", balance + draw))

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
# 7. Balances -- end-of-day snapshots on primary accounts, separate from
#    the transaction-level running balance. Real banking data warehouses
#    almost always expose EOD balance as its own fact table rather than
#    expecting consumers to reconstruct it from a raw transaction feed.
# ----------------------------------------------------------------------
def gen_balances(transactions: pd.DataFrame, accounts: pd.DataFrame):
    primary_ids = set(accounts[accounts["is_primary"]]["account_id"])
    tx = transactions[transactions["account_id"].isin(primary_ids)].copy()
    tx["booking_date"] = pd.to_datetime(tx["booking_date"])

    rows = []
    for account_id, grp in tx.groupby("account_id"):
        daily_close = grp.sort_values("booking_date").groupby("booking_date")["balance_after"].last()
        full_idx = pd.date_range(daily_close.index.min(), daily_close.index.max(), freq="D")
        closing = daily_close.reindex(full_idx).ffill()
        opening = closing.shift(1).fillna(closing.iloc[0])
        for dt, close_bal, open_bal in zip(full_idx, closing.values, opening.values):
            rows.append({
                "account_id": account_id,
                "date": dt.date().isoformat(),
                "opening_balance": round(float(open_bal), 2),
                "closing_balance": round(float(close_bal), 2),
            })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# 8. CRM / campaign interactions -- source of the "did this trigger
#    convert" label an ML scoring model would need; without this table
#    there is no honest way to generate training labels from data alone.
# ----------------------------------------------------------------------
OFFER_BY_TRIGGER = {
    "TENDER_PAYMENT": "trade_finance_facility",
    "CASHFLOW_STRESS": "overdraft_extension",
    "FX_EXPOSURE_NEW": "fx_hedging_product",
    "TREASURY_SURPLUS": "term_deposit",
    "NEW_COUNTERPARTY_CONCENTRATION": "cash_management_product",
    "CREDIT_UTILIZATION_SPIKE": "credit_line_review",
    "DORMANT_REACTIVATION": "relationship_review",
    "RECURRING_REVENUE_ESTABLISHED": "collections_product",
}
CHANNELS = ["RM_call", "branch_meeting", "email_campaign", "digital_portal_offer"]


def gen_crm_interactions(triggers: pd.DataFrame, clients: pd.DataFrame):
    rows = []
    inum = 0
    for _, t in triggers.iterrows():
        if rng.random() >= 0.75:
            continue
        inum += 1
        event_date = date.fromisoformat(t["event_date"])
        interaction_date = event_date + timedelta(days=int(rng.integers(3, 26)))
        if interaction_date > END_DATE:
            continue
        outcome = rng.choice(["accepted", "declined", "no_response", "pending"],
                              p=[0.32, 0.28, 0.30, 0.10])
        rows.append({
            "interaction_id": f"INT{inum:06d}",
            "client_id": t["client_id"],
            "date": interaction_date.isoformat(),
            "channel": rng.choice(CHANNELS),
            "campaign_id": f"CMP-{interaction_date.year}-{OFFER_BY_TRIGGER[t['trigger_type']]}",
            "offer_type": OFFER_BY_TRIGGER[t["trigger_type"]],
            "linked_trigger_type": t["trigger_type"],
            "outcome": outcome,
        })

    # baseline routine relationship-management noise, unrelated to any trigger
    n_noise = 150
    client_ids = clients["client_id"].values
    for _ in range(n_noise):
        inum += 1
        cid = rng.choice(client_ids)
        d = START_DATE + timedelta(days=int(rng.integers(0, (END_DATE - START_DATE).days)))
        rows.append({
            "interaction_id": f"INT{inum:06d}",
            "client_id": cid,
            "date": d.isoformat(),
            "channel": rng.choice(CHANNELS),
            "campaign_id": f"CMP-{d.year}-generic_relationship",
            "offer_type": "generic_relationship_review",
            "linked_trigger_type": "",
            "outcome": rng.choice(["accepted", "declined", "no_response", "pending"],
                                   p=[0.12, 0.30, 0.48, 0.10]),
        })
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(PROTECTED_DIR, exist_ok=True)

    print("Generating clients + entity groups...")
    clients, groups = gen_clients()

    print("Generating accounts...")
    accounts = gen_accounts(clients)

    print("Generating facilities (product holdings)...")
    facilities = gen_facilities(clients, accounts)

    print("Generating transactions (this can take a minute)...")
    transactions, triggers = gen_transactions(clients, accounts)
    transactions = transactions.sort_values(["client_id", "booking_date"]).reset_index(drop=True)

    print("Linking credit-utilization spikes to facilities...")
    facilities = apply_utilization_spikes(facilities, triggers)

    print("Deriving end-of-day balances...")
    balances = gen_balances(transactions, accounts)

    print("Generating risk ratings...")
    trigger_log_records = triggers.to_dict("records")
    risk_ratings = gen_risk_ratings(clients, trigger_log_records)

    print("Generating CRM/campaign interactions...")
    crm_interactions = gen_crm_interactions(triggers, clients)

    groups.to_csv(os.path.join(OUT_DIR, "entity_groups.csv"), index=False)
    clients.to_csv(os.path.join(OUT_DIR, "clients.csv"), index=False)
    accounts.to_csv(os.path.join(OUT_DIR, "accounts.csv"), index=False)
    facilities.to_csv(os.path.join(OUT_DIR, "facilities.csv"), index=False)
    risk_ratings.to_csv(os.path.join(OUT_DIR, "risk_ratings.csv"), index=False)
    transactions.to_csv(os.path.join(OUT_DIR, "transactions.csv"), index=False)
    balances.to_csv(os.path.join(OUT_DIR, "balances.csv"), index=False)
    crm_interactions.to_csv(os.path.join(OUT_DIR, "crm_interactions.csv"), index=False)
    triggers.sort_values(["client_id", "event_date"]).to_csv(
        os.path.join(PROTECTED_DIR, "trigger_events.csv"), index=False
    )

    print(f"Entity groups:      {len(groups):>8,}")
    print(f"Clients:            {len(clients):>8,}")
    print(f"Accounts:           {len(accounts):>8,}")
    print(f"Facilities:         {len(facilities):>8,}")
    print(f"Risk ratings:       {len(risk_ratings):>8,}")
    print(f"Transactions:       {len(transactions):>8,}")
    print(f"Balances (EOD):     {len(balances):>8,}")
    print(f"CRM interactions:   {len(crm_interactions):>8,}")
    print(f"Trigger events (ground truth): {len(triggers):>8,}")
    print(f"Written to {OUT_DIR}/")


if __name__ == "__main__":
    main()
