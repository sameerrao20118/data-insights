# NatWest FDM Model & DataInsights Alignment

**See also:** `docs/three_layers_schema_binding.md` (schema binding mechanics), `docs/fdm_reference.md` (complete FDM DDL capture), `docs/architecture.md` (pipeline integration)

This document explains how NatWest's Federated Data Model (FDM) relates to your DataInsights detector logic and why the legacy schema is FDM-aligned.

---

## What is FDM?

**FDM = Federated Data Model**

A **semantic model** that captures **what data means to people** (business meaning) rather than how it's implemented in systems. Version FDM V1.6, governed by NatWest's Data Models Working Group.

**Why "Federated"?** Decentralized approach:
- Central **Kernel** (8 non-negotiable classes used everywhere)
- **Shared Extensions** (domain-neutral enrichments)
- **Domain-Specific PDMs** (platform-specific implementations per business area)

Each domain (Deposits, Lending, Treasury, Risk, FinCrime) manages its own data product independently while adhering to shared FDM standards.

---

## The 8 Kernel Classes (Core Banking Concepts)

These are canonical business entities every banking system needs:

| Kernel Class | Represents | Example | Key ID |
|---|---|---|---|
| **Party** | Customers, counterparties, legal entities | A corporate customer (Unilever Ltd) | `PRTY_ID` |
| **Arrangement** | Agreements, facilities, treasury arrangements | A credit line or overdraft | `AGRMNT_ID` |
| **Account** | Financial accounts, balances | An operating account | (part of Arrangement) |
| **Product** | Credit products, treasury products | "Business Overdraft" or "Treasury Bond" | `PRDCT_ID` |
| **Event** | Transactions, payment events, balance snapshots | A payment or daily balance | `EVNT_ID` |
| **Party Asset** | Collateral, securities, documentation | Property or equipment collateral | `PRTY_ASSET_ID` |
| **Condition** | Interest rates, APR, pricing, limits | "3% annual" or "£50k limit" | (varies) |
| **Locator** | Addresses, locations, geographies | "London, UK" or "HQ" | (varies) |

### Kernel Relationship Diagram

```
                    PARTY (Client/Customer)
                       ↓
                  PARTY_GROUP (hierarchy)
                   ↙            ↘
            LOCATOR         PARTY_ASSET (Collateral)
                   ↓              ↓
            ARRANGEMENT (Facility/Loan)    ← PRODUCT
                   ↓
                ACCOUNT (Balance)
                   ↓
                 EVENT (Transaction)
                   ↓
             CONDITION (Rates/Limits)
```

---

## Five Banking Domains (Endogenous Signal Sources)

The FDM is organized by business domain. Each domain sees the same client through a different lens and contributes endogenous signals (internal data) to the ranking decision.

### Domain 1: Deposits & Cash Management

**What it tracks:** Customer deposits, cash balances, payment flows

**Physical tables (Snowflake):**
- `AGREEMENT_DAILY_BALANCE` — daily ledger balance per account
- `EVENT_FINANCIAL` — all credit/debit transactions  
- `PARTY_AGREEMENT` — which parties own which accounts

**Endogenous signals** (detector input):
- `large_incoming_payment` — transaction exceeds 90-day rolling baseline → **FINANCING_NEED**
- `cash_buildup` — ledger balance trending up 30+ days → **TREASURY_OPPORTUNITY**
- `dormancy` — zero transactions 90+ days → **ADVISORY_ONLY**
- `recurring_revenue_change` — credit frequency/amount shift → **FINANCING_NEED** (growth)

**Your legacy schema equivalent:**
```yaml
# config/entities.yaml
transactions:
  physical_table: transactions
  required_columns:
    amount: {type: decimal}          # large_incoming_payment signal
    booking_date: {type: date}       # dormancy signal (time window)

balances:
  physical_table: balances
  required_columns:
    closing_balance: {type: decimal} # cash_buildup signal
```

**Exogenous amplifiers** (external events that strengthen signal):
- Tender award (TED API) + large incoming payment = confirmed FINANCING_NEED
- ECB rate cut + cash buildup = amplified TREASURY_OPPORTUNITY

### Domain 2: Lending & Credit Facilities

**What it tracks:** Loans, credit lines, collateral, drawdowns

**Physical tables (Snowflake):**
- `AGREEMENT` — facility/loan agreement details
- `AGREEMENT_DAILY_BALANCE` — utilization vs. limit
- `COLLATERAL_ITEM_VALUE` — collateral valuation over time
- `MORTGAGE_AGREEMENT` — fixed-rate expiry, LTV

**Endogenous signals:**
- `facility_utilization_spike` — daily drawdown > 85% of limit → **FINANCING_NEED**
- `facility_maturity` — close date < 90 days → **FINANCING_NEED** (renewal)
- `collateral_coverage_drop` — collateral value vs. exposure → **RISK_REVIEW**
- `fixed_rate_expiry` — mortgage rate fixes approaching → **TREASURY_OPPORTUNITY** or **HEDGING_NEED**

**Your legacy schema equivalent:**
```yaml
accounts:
  physical_table: accounts
  required_columns:
    account_type: {type: string, allowed: [current, savings, credit_facility]}
    credit_limit: {type: decimal}    # facility_utilization = balance/limit
    close_date: {type: date}         # facility_maturity signal
```

**Exogenous amplifiers:**
- Rate rise + floating-rate facility = amplified HEDGING_NEED
- Tender award + facility maturity = CAPEX_FINANCING opportunity

### Domain 3: Balance Sheet / Group Treasury (TILAPI)

**What it tracks:** Group-level cash, intraday liquidity, FX exposure

**Endogenous signals:**
- `fx_exposure_building` — multi-currency positions → **HEDGING_NEED**
- `group_cash_pooling` — cash available for group consolidation → **TREASURY_OPPORTUNITY**

**Your legacy schema:** Not yet contracted (group hierarchy not in CSV)

### Domain 4: Risk & Credit Risk (CRADLE/IRB)

**What it tracks:** Risk ratings, PD (Probability of Default), LGD (Loss Given Default)

**Physical tables:**
- `PARTY` includes `RSK_GRD_CD` (risk grade) and `HIGH_RSK_CUST_IND` (risk flag)
- `PARTY_METRIC` — historical risk metrics
- `*_MODEL_DATA` tables — PD model outputs (LC_MODEL_DATA for Large Corporate, ML_MODEL_DATA for Mid-Large)

**Endogenous signals:**
- `rating_downgrade` — risk grade migration in PARTY → **RISK_REVIEW**
- `pd_migration` — PD model output shift → **RISK_REVIEW** or **FINANCING_NEED**
- `concentration_risk` — sector concentration in related parties → **RISK_REVIEW**

**Your legacy schema:** `clients.csv` exists but not contracted yet

### Domain 5: Economic Crime / Financial Crime (FCAMI) — HARD BOUNDARY

🚫 **DataInsights DOES NOT read this domain.**

**Why:**
1. Separate governance (FinCrime has own regulatory obligations and RM accountability)
2. NBA/EBM contamination (AML signals on a sales worklist conflates compliance and sales)
3. PEP boundary (combining PEP scoring with geopolitical events is prohibited)

**What IS allowed:** The `HIGH_RSK_CUST_IND` flag in `PARTY` is visible, but only as "flag exists" — not for correlating with event types or ranking recommendations.

---

## Six NBA Categories (Revenue Map)

Your six recommendation categories map directly to FDM domains:

| Category | Revenue? | Mechanism | Primary Signal | FDM Domain |
|---|---|---|---|---|
| **FINANCING_NEED** | ✅ Yes | Interest income + fees on new loan/credit line | Large payment, utilization spike, maturity | Deposits + Lending |
| **TREASURY_OPPORTUNITY** | ✅ Yes | Fee/spread from deposit or short-term investment | Cash buildup, fixed-rate expiry, rate moves | Deposits + Treasury |
| **HEDGING_NEED** | ✅ Yes | Fee from FX or commodity hedge | Multi-currency exposure, rate direction | Lending + Treasury |
| **CAPEX_FINANCING** | ✅ Yes | Interest income on equipment/capex financing | Asset appreciation, tender award, construction NACE | Lending + Treasury |
| **RISK_REVIEW** | ❌ Defensive | Protects existing revenue via compliance | Rating downgrade, collateral drop, sanctions | Risk domain |
| **ADVISORY_ONLY** | ❌ Relationship | Conversation worth having, no product yet | Dormancy, general macro, no sized action | Across domains |

---

## How Your Binding Maps to FDM

Your **legacy schema** is a **simplified, proof-of-concept version** of the FDM that proves detectors can be schema-agnostic.

### Example: Transaction → Event Concept

**FDM Kernel Class (Snowflake ENT_PRD.TIER0_PRS):**
```sql
CREATE TABLE EVENT_FINANCIAL (
  EVNT_ID NUMBER(38,0),              -- Event hub key
  AGRMNT_ID_TRN_ACCT NUMBER(38,0),   -- Which facility (FK to AGREEMENT)
  FIN_EVNT_AMT NUMBER(18,4),         -- Amount
  FIN_EVNT_CURY_CD VARCHAR(3),       -- Currency
  FIN_EVNT_PSTD_DT DATE,             -- Posted date
  -- + 75 more columns (counterparty, channel, reference, settlement, etc.)
);
```

**Your legacy CSV:**
```
transaction_id,account_id,amount,currency,booking_date
T-001,AC-123,5000,EUR,2026-01-15
```

**Your binding** (`config/bindings/legacy.yaml`):
```yaml
concepts:
  Transaction:
    entity: transactions
    fields:
      transaction_id: transaction_id   # CSV → Canonical
      account_id: account_id           # CSV → Canonical
      amount: amount                   # CSV → Canonical
      currency: currency               # CSV → Canonical
      posted_at: booking_date          # CSV `booking_date` → Canonical `posted_at`
```

**Detector sees (canonical names, FDM-aligned):**
```python
# Detector uses FDM concept names
Transaction.posted_at    # = FDM's FIN_EVNT_PSTD_DT
Transaction.amount       # = FDM's FIN_EVNT_AMT
Transaction.currency     # = FDM's FIN_EVNT_CURY_CD
```

**Result:** Same detector logic works with **both** FDM Snowflake and legacy CSV.

---

## The Full Picture: FDM → Binding → Detector

```
NatWest Snowflake (ENT_PRD.TIER0_PRS)
    FDM Kernel Classes
    8 classes + 66+ tables
         ↓
    (e.g., EVENT_FINANCIAL with 81 columns)
         ↓
    ╔════════════════════════════════════╗
    ║ config/bindings/fdm.yaml           ║
    ║ Maps Snowflake → canonical concepts║
    ╚════════════════════════════════════╝
         ↓
    Detector uses:
    Transaction.posted_at, .amount, .currency
    (schema-agnostic, no Snowflake knowledge)


Your Legacy CSV (data_generator/output/)
    Simple 3-table structure
    transactions, accounts, balances
         ↓
    (e.g., transactions.csv with 10 columns)
         ↓
    ╔════════════════════════════════════╗
    ║ config/bindings/legacy.yaml        ║
    ║ Maps CSV → canonical concepts      ║
    ╚════════════════════════════════════╝
         ↓
    Same detector uses:
    Transaction.posted_at, .amount, .currency
    (same code works with both!)
```

---

## Why This Matters for Your Work

### What You're Proving (Phase 1: Legacy CSV)

You're building detectors against synthetic data that:
- **Runs independently** with the legacy binding (tests/test_legacy_binding_end_to_end.py proves this)
- Targets the same canonical `semantic_model.yaml` that FDM also targets (but neither depends on the other)
- Uses FDM Kernel Class concepts as *inspiration* (Party, Arrangement, Account, Event) — the concepts are useful business vocabulary, not a runtime dependency
- Gracefully degrades on missing FDM concepts (RiskGradeVersion, CollateralValuation) — tools return `not_available_under_this_binding`, never crash

**Goal:** Prove detector logic is schema-agnostic. FDM alignment is a **portability feature**, not a runtime requirement — when you later switch to `fdm_snowflake` profile, the same detector code runs unchanged. Legacy works perfectly fine standalone.

### What Happens in Production (Phase 2: FDM Snowflake)

When NatWest connects real Snowflake `ENT_PRD.TIER0_PRS`:
1. Create `config/entities_fdm.yaml` (maps to actual Snowflake tables)
2. Create `config/bindings/fdm.yaml` (maps FDM columns to canonical concepts)
3. **No detector changes** — existing code uses canonical concepts
4. Switch profile from `legacy_local` to `fdm_snowflake`
5. Same pipeline, same detectors, real FDM data

### Scaling to Other Sources (Phase 3+: AWS Glue, etc.)

Same pattern:
1. Create `config/entities_glue.yaml`
2. Create `config/bindings/glue.yaml`
3. Detector code stays the same

---

## Domains Not Yet Fully Aligned (Gaps)

Your legacy schema is intentionally **small vertical slice** (Deposits + Lending basics). FDM covers more:

| FDM Domain | Your Status | Gap | Solution |
|---|---|---|---|
| **Deposits** | ✅ Contracted (transactions, balances) | None | Ready for detector |
| **Lending** | ⚠️ Partial (accounts with types/limits) | No collateral, no mortgage details | Add clients/facilities/risk_ratings when needed |
| **Treasury** | ❌ Not contracted | No group hierarchy, no FX data | Add PARTY_GROUP entity + multi-currency account balances |
| **Risk** | ⚠️ Minimal (high_risk_flag in accounts) | No risk ratings, no PD models | Add risk_ratings.csv when detector needs it |
| **FinCrime** | 🚫 Hard boundary | None (intentional) | Separate governance, not part of DataInsights |

---

## How to Handle Misaligned Domains

See `docs/handling_domain_gaps.md` for detailed strategy.

**Short version:** When a detector needs a column not yet in `config/entities.yaml`:

1. **Check if column exists in physical data** (CSV or Snowflake)
2. **If yes:** Add to entity contract + binding → detector ready
3. **If no:** Check FDM spec for equivalent column → add to data generator → update contract
4. **If FDM gap:** Document as "not contracted" in comment, escalate to domain team

---

## References

- **Schema binding mechanics:** `docs/three_layers_schema_binding.md`
- **Complete FDM DDL:** `docs/fdm_reference.md`
- **Pipeline integration:** `docs/architecture.md`
- **Handling gaps:** `docs/handling_domain_gaps.md`
- **Config contracts:** `config/entities.yaml`, `config/bindings/legacy.yaml`

