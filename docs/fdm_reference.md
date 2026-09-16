# NatWest Group — Federated Data Model (FDM) & DataInsights Reference

Source: screenshots of an internal reference page (`fdm_reference.html`,
FDM V1.6, schema `ENT_PRD.TIER0_PRS`, status "Draft — SME Review Pending",
last crawled Sep 2026). Captured 2026-09-13 across 15 photos of the
"FDM Overview" and "Physical Models" tabs. This file exists to preserve
that content verbatim as reference/context for DataInsights work — it is
**not verified against a live Snowflake instance from this repo**, and per
`CLAUDE.md` no code here should assume Snowflake as anything but a
configurable source.

This is a living document — more domain screenshots will be added as they
come in. Each domain section below should stay self-contained so it can be
extended without renumbering.

## What is the FDM?

**Definition:** The FDM is a semantic model that captures what data means
to *people* rather than how it is implemented in *systems*. It is
solution-agnostic — defining meaning independent of any physical data
implementation.

- Version: FDM V1.6
- Governed by: Data Models Working Group (DMWG)
- Tooling: Sparx Enterprise Architect

**Architecture principle:** One Conceptual Model (CDM) → One Logical Model
(LDM) → Many Physical Models (PDMs). *"Define once, govern once, reuse
across applications."*

**Why federated?** A decentralised approach where business area teams own
and manage their Data Products and KDEs independently, while adhering to
shared standards. The central Kernel provides the non-negotiable spine;
domain teams grow their own specialised extensions from it. This optimises
translation of local business knowledge into the shared model, while
resolving common definitions in a controlled way.

Source: Confluence DAK space → FDM Overview (page 2764667073).

## FDM 3-tier classification

| Tier | Name | Description | Examples |
|---|---|---|---|
| 1 | FDM Kernel Classes | Centrally governed by DMWG. The non-negotiable spine. 8 classes used everywhere. | Party, Arrangement, Account, Product, Event, Locator, Party Asset, Condition |
| 2 | Shared Extension Classes | Domain-neutral extensions that enrich the Kernel without breaking it. | Party Role, Banking Arrangement, Account Metric, Treasury Arrangement |
| 3 | Domain-Specific PDMs | Platform-specific physical implementations. Each domain owns its own PDM. | C&I NPDM (cid_standardized), FCAMI (FSA_PRD_FINCRIME), TILAPI (Group Treasury) |

## FDM Kernel Classes — conceptual diagram

The 8 kernel classes and how they relate. All physical tables in
`ENT_PRD.TIER0_PRS` implement these relationships.

| Class | Covers | Key |
|---|---|---|
| Party | Customers, Counterparties, Legal Entities, Borrowers | `PRTY_ID` |
| Arrangement | Banking Agreements, Facilities, Treasury Arrangements | `AGRMNT_ID` |
| Account | Financial Accounts, Balances, Arrears | `AGRMNT_ID` |
| Product | Credit Facility Products, Treasury Products, Product Master | `PRDCT_ID` |
| Event | Financial Transactions, Payment Transactions, Balance Ingestion Events | `EVNT_ID` |
| Party Asset | Collateral, Securities, Regulatory Reporting Documentation | `PRTY_ASSET_ID` |
| Condition | Interest Rates, APR, Pricing, Limits, Thresholds | (no dedicated hub key shown) |
| Locator | Postal Addresses, Locations, Geographies | (no dedicated hub key shown) |
| Party Group | Group Hierarchy, Entity Relationships | `PRTY_GRP_ID` |

Relationship order shown in the diagram: `Party → Arrangement → Account →
Event → Condition`, with `Product` also feeding `Arrangement`/`Account`,
and `Party Group` / `Locator` as peripheral classes attached to `Party`.

## FDM Common Keys — enterprise join keys

Common keys are the FDM backbone. Every physical table in
`ENT_PRD.TIER0_PRS` uses these keys to join across domains. The
DataInsights agent joins are built on these.

| Key Column | FDM Class | Physical Tables | Child Count | DataInsights Use |
|---|---|---|---|---|
| `PRTY_ID` | Party | PARTY (hub) | 28 child tables | Client identifier for all endogenous detectors |
| `AGRMNT_ID` | Arrangement | AGREEMENT (hub) | 75 child entities | Facility join: balances, utilization, maturity |
| `PRDCT_ID` | Product | PRODUCT (hub) | 26 entities | Product type classification for category tagging |
| `EVNT_ID` | Event | EVENT_FINANCIAL (hub) | 40 entities | Transaction amount for `large_incoming_payment` detector |
| `PRTY_ASSET_ID` | Party Asset | PARTY_ASSET (hub) | 8 entities | Collateral value tracking for RISK_REVIEW |
| `CLTRL_ITEM_ID` | Collateral | COLLATERAL_ITEM (hub) | 9 entities | Collateral vs facility coverage ratio |
| `PRTY_GRP_ID` | Party Group | PARTY_GROUP (hub) | 4 entities | Group treasury analysis across legal entities |

## What FDM is used for

| Use Case | Description | DataInsights Relevance |
|---|---|---|
| Business data requirements | Define data needs independently of systems | Enables cross-domain agent design without system lock-in |
| Physical data understanding | Map physical tables to common meaning across systems | PARTY in `cid_standardized` = same concept as PARTY in ENT_PRD |
| Identity & grain | Combine datasets correctly via Common Key Standard | `PRTY_ID` as the anchor key for all client-level signals |
| Data Product scoping | Data Products scoped by FDM Key Data Class | Worklist output maps to EVENT class (recommendation = an event) |
| AI knowledge provisioning | Structured knowledge about data for AI agents | FDM definitions + physical DDL = agent knowledge base |
| Forward engineering | Generate physical schemas from business requirements | Synthetic dataset generator uses FDM structure to produce realistic tables |

---

## Physical Data Models — `ENT_PRD.TIER0_PRS`

- Database: `ENT_PRD` | Schema: `TIER0_PRS` | Platform: Snowflake | Source:
  Data Lakehouse (DLH) space, EDI-EDW team
- Status: Draft — pending SME sign-off per DLH space header notices
- **Standard pattern — all tables:** every table includes EDI audit
  columns: `EFFECTIVE_START/END_DT/TM`, `LAST_UPDATED_DT/TM`, `CRT_DT/TM`,
  `EDI_FD_ID`, `EDI_FD_RUN_ID`, `DTST_ID`, `REC_ID`. Bi-temporal Type 2 SCD
  + Hub-Satellite + XREF identity resolution throughout.

### PARTY Domain — 66 tables

Hub: `PARTY` (`PRTY_ID`, 28 children) + `AVA_PARTY` (`PARTY_ID`, 25
children). Version 2.1 — 2026-08-17. Source: DLH/2799606602.

**DataInsights relevance:** `PRTY_SGMNT_CD` = client segment
(SME/Mid-Corp/Large-Corp). `RSK_GRD_CD` + `RSK_GRD_VAL` = live risk rating.
`HIGH_RSK_CUST_IND` = elevated risk flag. `PARTY_AGREEMENT` joins to all
active facilities. `PARTY_METRIC` tracks historical metrics. These are the
core client identifiers for all endogenous signal detection.

```sql
-- Physical Data Model: PARTY Domain (ENT_PRD.TIER0_PRS) v2.1
CREATE OR REPLACE TABLE PARTY (
    PRTY_ID              NUMBER(38,0),   -- HUB PK: 28 child tables reference this
    PRTY_STRT_DTTM        TIMESTAMP_NTZ(0),
    PRTY_END_DTTM         TIMESTAMP_NTZ(0),
    PRTY_TYP_CD           VARCHAR(3),     -- Party type (Individual, Organisation)
    PRTY_SBTYP_CD         VARCHAR(3),     -- Sub-type
    RSK_GRD_CD            VARCHAR(3),     -- Risk grade code  <-- KEY: RISK_REVIEW detector
    RSK_GRD_VAL           NUMBER(38,0),   -- Numeric risk grade value
    RSK_GRD_DT            DATE,           -- When grade was assigned
    PRTY_SGMNT_CD         VARCHAR(3),     -- Segment: SME/MID/LARGE/INST
    PRTY_SGMNT_DT         DATE,
    MOBILE_BNKG_IND       VARCHAR(1),
    ONLINE_BNKG_IND       VARCHAR(1),
    TELPHN_BNKG_IND       VARCHAR(1),
    HIGH_RSK_CUST_IND     VARCHAR(1)      -- High risk indicator
    -- + 17 additional business cols + 12 standard audit cols
);

CREATE OR REPLACE TABLE AVA_PARTY (
    PARTY_ID                    NUMBER(38,0) NOT NULL,  -- HUB PK (Avaloq source)
    REF_DATE                    DATE NOT NULL,           -- Temporal snapshot key
    PARTY_AVALOQ_ALTERNATE_ID   NUMBER(38,0),
    PARTY_DESC                  VARCHAR(200),
    PARTY_TYPE_CD                VARCHAR(100),
    PARTY_SUBTYPE_CD              VARCHAR(100),
    BU_ID                        NUMBER(9,0)              -- Business Unit
    -- + 32 additional business cols + 12 audit cols
);
```

**PARTY Hub FK children (28 tables via `PRTY_ID`):**

| Child Table | Purpose | Signal Relevance |
|---|---|---|
| PARTY_AGREEMENT | Party-to-facility link | ⭐⭐ All facility lookups |
| PARTY_METRIC | Historical metrics per party | ⭐⭐ Trend detection |
| PARTY_IDENTIFICATION | LEI, CIN, identifiers | ⭐ Client ID resolution |
| PARTY_LOCATOR | Addresses & geography | ⭐ Country/region for exogenous matching |
| PARTY_STATUS | Active/inactive/dormant | ⭐ Filter inactive clients |
| PARTY_RELATED | Related party links | ⭐ Group exposure analysis |
| PARTY_TO_PARTY_ASSET | Client-to-collateral link | ⭐⭐ Collateral monitoring |
| PARTY_TO_PARTY_GROUP | Group membership | ⭐⭐ Entity hierarchy for treasury |
| EVENT_PARTY | Event-to-party link | ⭐⭐ Transaction ownership |
| PARTY_DEMOGRAPHIC | Firmographics, NACE codes | ⭐⭐ Sector for exogenous matching |
| PARTY_CURRENCY | Preferred/operating currencies | ⭐ FX exposure context |
| PARTY_BANKRUPTCY | Insolvency events | ⚠️ RISK_REVIEW trigger |

### AGREEMENT Domain — 75 entities

Facilities, Mortgages, Credit Lines, Daily Balances. Generated 2026-08-07.
Source: DLH/2780084329.

**DataInsights relevance:** `AGREEMENT_DAILY_BALANCE` tracks utilization
over time → facility utilization spike detector. `AGRMNT_ORIG_LIM` vs
current drawdown = headroom. `AGRMNT_CLOSE_DT` within 90 days = maturity
approaching detector. `PARTY_AGREEMENT` links client to all their
facilities. Subtypes (`MORTGAGE_AGREEMENT`, `LOAN_AGREEMENT`) provide
domain-specific attributes.

```sql
CREATE OR REPLACE TABLE AGREEMENT (
    AGRMNT_ID             NUMBER(38,0),   -- HUB PK: 75 child entities
    AGRMNT_NM              VARCHAR(100),
    AGRMNT_OPEN_DT         DATE,
    AGRMNT_CLOSE_DT        DATE,           -- ⭐⭐ Maturity approaching detector
    AGRMNT_TYP_CD          VARCHAR(3),     -- Facility type (loan, overdraft, trade finance)
    AGRMNT_SBTYP_CD        VARCHAR(3),
    AGRMNT_ORIG_LIM        NUMBER(18,4),   -- ⭐⭐ Original limit (for utilization %)
    AGRMNT_RPMNT_TYP_CD    VARCHAR(3),     -- Repayment type
    AGRMNT_RPMNT_FREQ_CD   VARCHAR(3),
    AGRMNT_BANK_CD         VARCHAR(3),
    AGRMNT_IBAN_NUM        VARCHAR(50),    -- ⭐ IBAN (currency & country signals)
    AGRMNT_PURP_CD         VARCHAR(10)     -- Purpose code
    -- + 40+ additional business columns
);

CREATE OR REPLACE TABLE AGREEMENT_DAILY_BALANCE (
    AGRMNT_ID                    NUMBER(38,0),   -- FK to AGREEMENT
    AGRMNT_DLY_BAL_STRT_DTTM     TIMESTAMP_NTZ(0),
    AGRMNT_DLY_BAL_END_DTTM      TIMESTAMP_NTZ(0),
    AGRMNT_CLRD_BAL_FOR_INT_AMT  NUMBER(18,4),   -- ⭐⭐ Cleared balance (interest)
    AGRMNT_UNCLRD_BAL_AMT        NUMBER(18,4),   -- Uncleared balance
    AGRMNT_LDGR_BAL_AMT          NUMBER(18,4),   -- ⭐⭐ Ledger balance (cash buildup)
    AGRMNT_CAPTL_BAL_AMT         NUMBER(18,4),   -- Capital balance
    AGRMNT_BAL_CURY_CD           VARCHAR(3)      -- Currency
);

CREATE OR REPLACE TABLE PARTY_AGREEMENT (
    PRTY_ID                NUMBER(38,0),   -- FK -> PARTY hub
    AGRMNT_ID               NUMBER(38,0),   -- FK -> AGREEMENT
    PRTY_AGRMNT_ROLE_CD     VARCHAR(3),     -- Role: borrower, guarantor, director
    PRTY_AGRMNT_STRT_DTTM   TIMESTAMP_NTZ(0),
    PRTY_AGRMNT_END_DTTM    TIMESTAMP_NTZ(0),
    PRTY_AGRMNT_RSN_CD      VARCHAR(3)
);

-- Subtype extensions
CREATE OR REPLACE TABLE MORTGAGE_AGREEMENT (
    AGRMNT_ID                  NUMBER(38,0),
    MORT_ORIG_LTV_PCT          NUMBER(18,4),   -- Original LTV
    MORT_CRRNT_LTV_PCT         NUMBER(18,4),   -- Current LTV ⭐⭐
    MORT_PRPTY_CRRNT_VAL_AMT   NUMBER(18,4),   -- Current property value
    MORT_REPYMNT_TYP_CD        VARCHAR(3),
    MORT_TERM_MTHS_CNT         NUMBER(38,0),
    MORT_RMNG_TERM_MTHS_CNT    NUMBER(38,0),   -- Remaining term
    MORT_FXED_RT_END_DT        DATE,           -- ⭐⭐ Fixed rate expiry
    MORT_BUY_TO_LET_IND        VARCHAR(1)
);
```

**Source systems** (from Physical Models tab, applies broadly):

| Code | System | Domain |
|---|---|---|
| AVA | Avaloq Core Banking Platform | Agreement Management |
| EDGS | Eligibility Data Governance Service | Agreement Eligibility |
| MTA | Multi-Tenant Architecture | Agreement Management |
| CDS | Customer Data Service | Classification |

### EVENT Domain V2 — 40 entities

Financial Transactions, AML Alerts, Validation Events. Version 2, Generated
2026-08-28. Source: DLH/2826095493.

**DataInsights relevance:** `EVENT_FINANCIAL.FIN_EVNT_AMT` is the core
signal for the `large_incoming_payment` detector. Join `EVNT_ID` →
`EVENT_PARTY` → `PRTY_ID` to get the client. `FIN_EVNT_CURY_CD` enables
multi-currency EUR normalisation. 81 business columns in `EVENT_FINANCIAL`
give full transaction context including channel, settlement, and
counterparty.

> ⚠️ **`AML_ALERT_EVENT`:** This entity is in the EVENT domain but the AML
> signal it carries belongs to the FinCrime governance path (FCAMI).
> **DataInsights reads `EVENT_FINANCIAL` only — never `AML_ALERT_EVENT`.**
> This is consistent with the `CLAUDE.md` hard boundary on never reading
> ground-truth/label-adjacent data outside its designated role.

```sql
CREATE OR REPLACE TABLE EVENT_FINANCIAL (
    FIN_EVNT_PSTD_DT        DATE NOT NULL,       -- Posted date
    AGRMNT_SBTYP_CD          VARCHAR(3) NOT NULL,
    EVNT_ID                  NUMBER(38,0) NOT NULL,  -- HUB PK
    AGRMNT_ID_BAL_ACCT       NUMBER(38,0),        -- Balance account FK
    AGRMNT_ID_TRN_ACCT       NUMBER(38,0),        -- ⭐⭐ Transaction account FK
    FIN_EVNT_SBTYP_CD        VARCHAR(3),          -- Transaction sub-type
    FIN_EVNT_TYP_ID          NUMBER(38,0),        -- Transaction type
    FIN_EVNT_AMT             NUMBER(18,4),        -- ⭐⭐⭐ AMOUNT: core detection signal
    FIN_EVNT_CURY_CD         VARCHAR(3),          -- Currency (EUR/GBP/USD...)
    FIN_EVNT_STLMNT_AMT      NUMBER(18,4),        -- Settlement amount
    FIN_EVNT_STLMNT_CURY_CD  VARCHAR(3)
    -- + 68 more business columns (counterparty, channel, reference, etc.)
);

-- Hub entity linking events to all other domains
CREATE OR REPLACE TABLE AVA_EVENT (
    EVENT_ID    NUMBER(38,0) NOT NULL,  -- Avaloq event hub PK
    REF_DATE    DATE NOT NULL
    -- 13 child satellite tables via EVENT_ID
);

-- AML domain -- read by FinCrime governance only
CREATE OR REPLACE TABLE AML_ALERT_EVENT (
    EVNT_ID           NUMBER(38,0),
    EVNT_DESC          VARCHAR(250),
    EVNT_STRT_DTTM     TIMESTAMP_NTZ(0),
    EVNT_ACTVTY_TYP_CD VARCHAR(3),
    EVNT_RSN_CD        VARCHAR(64),
    EVNT_SBTYP_CD      VARCHAR(3),
    EVNT_TXT           VARCHAR(1000),
    SRC_FEED_CD        VARCHAR(50)      -- PTF = Firco AML screening
);
```

**EVENT FK children (via `EVNT_ID`):**

| Child | Purpose | DataInsights Use |
|---|---|---|
| EVENT_PARTY | Event-to-party relationship | ⭐⭐ `EVNT_ID → PRTY_ID` join |
| EVENT_METRIC | Event metrics and scores | ⭐ Additional signal context |
| EVENT_STATUS | Event lifecycle status | Filter confirmed transactions |
| EVENT_LOCATOR | Geographic location of event | ⭐ Country context |
| EVENT_GROUP | Grouped event sets | Batch/sweep detection |
| AML_ALERT_EVENT | AML screening results | ⚠️ FinCrime only — not used |

### PRODUCT Domain — 26 entities, 229 business columns

All Product Types. Generated 2026-08-20. Source: DLH/2779513514.

**DataInsights relevance:** `PRDCT_SBTYP_CD` identifies product category.
`PRODUCT_INT_RT_BAL_SCHED` + `PRODUCT_INT_RT_DUR_SCHED` = rate sensitivity
context for TREASURY_OPPORTUNITY when rates move. `PRDCT_WTHDRWL_DT` =
product maturity. `DEPOSIT_PRODUCT` and `LOAN_PRODUCT` subtypes carry
domain-specific fields.

```sql
CREATE OR REPLACE TABLE PRODUCT (
    PRDCT_ID                  NUMBER(38,0) NOT NULL,
    PRDCT_SBTYP_CD             VARCHAR(3) NOT NULL,   -- Sub-type code
    PRDCT_DESC                 VARCHAR(1500) NOT NULL,
    PRDCT_NM                   VARCHAR(100),
    PRDCT_STRT_DTTM            TIMESTAMP_NTZ(0),
    PRDCT_END_DTTM             TIMESTAMP_NTZ(0),
    BRND_CD                    VARCHAR(3),      -- Brand (NW/RBS/UB)
    PRDCT_INT_RT_SCHED_TYP_CD  VARCHAR(3),      -- Rate schedule type
    PRDCT_INT_RT_CALC_MTHD_CD  VARCHAR(3),      -- Rate calculation method
    PRDCT_INT_RT_ACRL_MTHD_CD  VARCHAR(3),      -- Accrual method
    PRDCT_ANNUL_FEE_AMT        NUMBER(18,4),    -- Annual fee
    PRDCT_LAUNCH_DT            DATE,
    PRDCT_WTHDRWL_DT           DATE             -- Product withdrawal date
    -- + 25 additional eligibility/feature columns
);

-- Product specialisations
-- DEPOSIT_PRODUCT, LOAN_PRODUCT, MORTGAGE_PRODUCT,
-- MTA_PRODUCT (Money Transmission), CREDIT_CARD_PRODUCT
```

**FK relationships:**

| Parent | Child | Cardinality |
|---|---|---|
| PRODUCT | DEPOSIT_PRODUCT | 1:0..N |
| PRODUCT | LOAN_PRODUCT | 1:0..N |
| PRODUCT | MTA_PRODUCT | 1:0..N |
| PRODUCT | MORTGAGE_PRODUCT | 1:0..N |
| PRODUCT | PRODUCT_INT_RT_BAL_SCHED | 1:0..N |
| PRODUCT | PRODUCT_INT_RT_DUR_SCHED | 1:0..N |
| PRODUCT | AGREEMENT_PRODUCT | 1:0..N |
| PRODUCT | PRODUCT_STATUS | 1:0..N |

### PARTY_ASSET + COLLATERAL Domains

Asset Values, Collateral, Securities. PARTY_ASSET: 2026-08-26. COLLATERAL:
2026-07-10. Sources: DLH/2822575435, DLH/2747477010.

**DataInsights relevance:** `ASSET_VALUE.ASSET_VAL_AMT` over time →
collateral value movement detector. `PARTY_TO_PARTY_ASSET` links client to
their assets. `COLLATERAL_ITEM_VALUE` vs `AGRMNT_ORIG_LIM` = coverage ratio
for RISK_REVIEW. `AVA_AGREEMENTASSET` links assets to specific facilities.

```sql
CREATE OR REPLACE TABLE PARTY_ASSET (
    PRTY_ASSET_ID       NUMBER(38,0),
    ASSET_DESC           VARCHAR(250),
    PRTY_ASSET_SBTYP_CD  VARCHAR(3),      -- Asset sub-type (property, vehicle, etc)
    ASSET_HOST_ID_VAL    VARCHAR(100)
);

CREATE OR REPLACE TABLE ASSET_VALUE (
    PRTY_ASSET_ID         NUMBER(38,0),   -- FK -> PARTY_ASSET
    ASSET_VAL_STRT_DTTM    TIMESTAMP_NTZ,
    ASSET_VAL_END_DTTM     TIMESTAMP_NTZ,
    ASSET_VAL_AMT          NUMBER(38,0),  -- ⭐⭐ Asset value over time
    ASSET_VALUTN_MTHD_CD   TEXT,          -- Valuation method
    CURY_CD                TEXT
);

CREATE OR REPLACE TABLE PARTY_TO_PARTY_ASSET (
    PRTY_ID              NUMBER(38,0),   -- FK -> PARTY
    PRTY_ASSET_ID         NUMBER(38,0),   -- FK -> PARTY_ASSET
    ASSET_ROLE_CD          VARCHAR(3),    -- Owner, guarantor, mortgagor
    ASSET_ROLE_STRT_DTTM   TIMESTAMP_NTZ(0),
    ASSET_ROLE_END_DTTM    TIMESTAMP_NTZ(0)
);

-- COLLATERAL DOMAIN (9 tables)
-- COLLATERAL_ITEM -> AGREEMENT_COLLATERAL_ITEM (links to facility)
--                  -> COLLATERAL_ITEM_VALUE (current valuation)
--                  -> COLLATERAL_ITEM_STATUS (active/released/substituted)
--                  -> COLLATERAL_ITEM_ID_XREF (source system cross-ref)
-- Sources: CMS, GMS (mortgage platforms), MMR/MMU (legacy)
```

### PARTY_GROUP Domain

Group Hierarchy, Entity Relationships. Generated 2026-08-26. Source:
DLH/2822103218. Source system: CDB (Customer Database).

**DataInsights relevance:** `PRTY_GRP_PARENT_ID` enables group-level
treasury analysis. If one entity in a group has a large cash position, the
group treasurer is the RM's contact. Critical for FX exposure and cash
pooling opportunity detection across legal entities.

```sql
CREATE OR REPLACE TABLE PARTY_GROUP (
    PRTY_GRP_ID          NUMBER(38,0),
    PRTY_GRP_PARENT_ID    NUMBER(38,0),   -- Self-referential hierarchy
    PRTY_GRP_DESC         VARCHAR(100),
    PRTY_GRP_NM           VARCHAR(50),
    PRTY_GRP_TYP_CD       VARCHAR(3)
);

CREATE OR REPLACE TABLE PARTY_TO_PARTY_GROUP (
    PRTY_ID                    NUMBER(38,0),   -- FK -> PARTY
    PRTY_GRP_ID                 NUMBER(38,0),   -- FK -> PARTY_GROUP
    PRTY_TO_PRTY_GRP_ROLE_CD    VARCHAR(3)      -- Role in group
);

CREATE OR REPLACE TABLE ORGN_POSN_TO_PARTY_GROUP (
    ORGN_POSN_ID                  NUMBER(38,0),   -- Organisation position
    PRTY_GRP_ID                    NUMBER(38,0),
    ORGN_POSN_TO_PRTY_GRP_ROLE_CD  VARCHAR(3)
);
```

---

---

## Five Banking Domains — Endogenous Signal Sources

This section is from the **Domain Data Products** tab (Tab 3 — see "Page
structure summary" near the end of this doc; originally misattributed to
Agent Signal Map when first captured). Each domain is a lens
on the same client. Cross-domain agents read physical tables, detect
endogenous signals, and correlate them with exogenous events. The combined
picture makes the RM hypothesis stronger and more defensible. **The
physical DDL in each domain is what the synthetic data generator must
replicate.**

Endogenous signal names below are the detector/signal identifiers as
labeled on the reference page (e.g. `large_incoming_payment`,
`facility_utilization_spike`) — treat these as the canonical signal names
to align detector code and the data generator against.

### Domain 1 — Deposits & Cash Management

- **FDM classes:** Account (Balance, Metric) · Event (Financial
  Transaction) · Arrangement (Banking) · Party
- **Physical location:** Snowflake: `cid_standardized` (C&I NPDM) ·
  `ENT_PRD.TIER0_PRS`
- **Key physical tables:**
  - `AGREEMENT` + `AGREEMENT_DAILY_BALANCE` → cash balances over time
  - `EVENT_FINANCIAL` → all credit/debit transactions
  - `PARTY_AGREEMENT` → client-to-account link
  - `PRODUCT` (`MTA_PRODUCT`, `DEPOSIT_PRODUCT`)
  - `cid_standardized`: C&I MTA Products, C&I Account Master
- **Source systems:** CustDB · AccountDB · Phoenix+ · International ·
  Potter · Bankline
- **Endogenous signals:**
  - `large_incoming_payment` — `EVENT_FINANCIAL.FIN_EVNT_AMT` vs 90d
    rolling MAD baseline → `FINANCING_NEED` or `TREASURY_OPPORTUNITY`
  - `cash_buildup` — `AGRMNT_LDGR_BAL_AMT` trending up 30+ days →
    `TREASURY_OPPORTUNITY`
  - `dormancy` — zero `EVENT_FINANCIAL` for 90+ days → `ADVISORY_ONLY`
  - `recurring_revenue_change` — credit transaction frequency/amount
    shift → `FINANCING_NEED` (growth)
- **Exogenous amplifier:** ECB rate cut + high cash balance = amplified
  `TREASURY_OPPORTUNITY`. Tender award + large credit = confirmed
  `FINANCING_NEED`.

### Domain 2 — Lending & Credit Facilities

- **FDM classes:** Arrangement (Lending, Mortgage) · Product (Credit
  Facility) · Condition (Limits, APR) · Party Asset (Collateral)
- **Physical location:** `ENT_PRD.TIER0_PRS` · Snowflake:
  `cid_standardized` (C&I NPDM)
- **Key physical tables:**
  - `AGREEMENT` (`AGRMNT_TYP_CD`=lending, `AGRMNT_CLOSE_DT`)
  - `AGREEMENT_DAILY_BALANCE` → utilization vs `AGRMNT_ORIG_LIM`
  - `COLLATERAL_ITEM` + `COLLATERAL_ITEM_VALUE`
  - `AGREEMENT_COLLATERAL_ITEM` → facility-to-collateral link
  - `MORTGAGE_AGREEMENT` → LTV, remaining term, fixed rate expiry
  - `cid_standardized`: Non Personal Loan Products
- **Source systems:** Loan IQ · PRisM · AMBIT · BBLJ · Lombard · RMP
- **Endogenous signals:**
  - `facility_utilization_spike` — daily drawdown > 85% of
    `AGRMNT_ORIG_LIM` → `FINANCING_NEED`
  - `facility_maturity` — `AGRMNT_CLOSE_DT` < 90 days →
    `FINANCING_NEED` (renewal)
  - `collateral_coverage_drop` — `COLLATERAL_ITEM_VALUE` vs exposure →
    `RISK_REVIEW`
  - `fixed_rate_expiry` — `MORT_FXED_RT_END_DT` approaching →
    `TREASURY_OPPORTUNITY` or `HEDGING_NEED`
- **Exogenous amplifier:** Rate rise + floating rate facility →
  `HEDGING_NEED`. Tender award → `CAPEX_FINANCING`. Energy shock →
  amplifies collateral coverage drop for energy sector clients.

### Domain 3 — Balance Sheet / Group Treasury (TILAPI)

- **FDM classes:** Account (Reserve Collateral, Intraday, Headroom) ·
  Arrangement (Treasury, Netting) · Event (Balance Ingestion) · Product
  (Risk Control)
- **Physical location:** Snowflake: **TILAPI** (Group Treasury space) ·
  BoE RTGS/CHAPS feeds
- **Key entities (TILAPI FDM):**
  - Reserve Collateral Account (RCA) → BoE Accounts API
  - Intraday Account Balance (SOD/10-min refresh)
  - Bilateral Net Position (BERTI) → BoE BERTI API
  - Intraday Liquidity Headroom → TILAPI derived metric
  - Bilateral Intraday Credit Limit → Group Treasury Risk Policy
- **FDM common keys:** `Party_ID`/LEI/BIC · `Arrangement_ID` (settlement)
  · `Account_ID`/Central_Bank_Acc_Ref · `Correlation_ID`/Task_ID
- **Endogenous signals:**
  - `fx_exposure_building` — multi-currency positions across
    `AGREEMENT.AGRMNT_IBAN_NUM` → `HEDGING_NEED`
  - `group_cash_pooling` — `PARTY_GROUP` hierarchy +
    `AGREEMENT_DAILY_BALANCE` → `TREASURY_OPPORTUNITY`
  - `liquidity_headroom_drop` — TILAPI headroom → `ADVISORY_ONLY`
- **Exogenous amplifier:** Energy price shock + commodity NACE → FX
  review. Rate rise + floating positions → rate-lock
  `TREASURY_OPPORTUNITY`.
- Source: GRPTREASURY/2853928962

### Domain 4 — Risk & Credit Risk (CRADLE/IRB)

- **FDM classes:** Account Metric (PD, LGD, DoD) · Condition (Credit
  Limits) · Party Asset (Regulatory Reporting)
- **Physical location:** AWS S3 (CRADLE pipeline) → Data Marketplace
  (DMP) · Snowflake via OBDQ
- **IRB Wholesale PD models (20 models total):**

  | Model | Code | Table |
  |---|---|---|
  | Large Corporate | LC | LC_MODEL_DATA |
  | Mid-Large Corporate | ML | ML_MODEL_DATA |
  | Banks | BK | BANK_MODEL_DATA |
  | Insurance | IN | INSURANCE_MODEL_DATA |
  | Project Finance | PF | PF_MODEL_DATA |
  | Property | PC | PC_MODEL_DATA |
  | Sovereign Entity | SD | SD_MODEL_DATA |
  | Shipping | SG | SG_MODEL_DATA |

  + 12 further models (Hedge Funds, Housing Association, Managed Fund,
  etc.) — full list not yet captured.

- **Endogenous signals:**
  - `rating_downgrade` — `RSK_GRD_CD` migration in `PARTY` →
    `RISK_REVIEW`
  - `pd_migration` — model output shifts in `LC/ML_MODEL_DATA` → early
    `FINANCING_NEED` or `RISK_REVIEW`
  - `concentration_risk` — `PARTY_RELATED` sector concentration →
    `RISK_REVIEW`
- **Exogenous amplifier:** Sanctions change + high-PD client in affected
  sector = amplified `RISK_REVIEW`. Natural disaster in client country +
  high LGD = combined `CAPEX_FINANCING` + `RISK_REVIEW`.
- Sources: FDAA/2641644577 · FDAA/2625647922

### Domain 5 — Economic Crime / Financial Crime (FCAMI) — HARD BOUNDARY

> 🚫 **This domain is a hard boundary for DataInsights.** It reinforces
> the `CLAUDE.md` rule to keep source/detector/ranking/narrative/evaluator
> roles separate and never blend FinCrime/AML signals into NBA/EBM
> ranking. Preserved here in full because the *reasoning*, not just the
> rule, matters for future contributors.

- **Physical location:** Snowflake: **FSA_PRD_FINCRIME** (FCAMI Data Mart)
- **Schema breakdown** (verified, source: `~david.campbell/2827139597`):

  | Schema | Tables | Views | Purpose |
  |---|---|---|---|
  | RAW | 2,835 | 51 | Source data as-landed from S3/BDI |
  | INT | 829 | 69 | Integration, cross-source joins, business logic |
  | PRS | 461 | 58 | Presentation layer for analyst consumption |
  | PEP_PRS | 52 | 0 | PEP model outputs, scoring, bulk closures |
  | GENESIS_STR_PRS | 1 | 1 | Genesis strategic case reporting |

  Total: 4,178 base tables, 178 views across 5 active schemas.

- **Key source prefixes:** SAM (Actimize SAM 9, AML monitoring) · ACTONE
  (Actimize ActOne, case management) · MON (Oracle FCRM) · CDDHUBBLE
  (CDD/KYC) · GCS (Global Client Screening) · MANTAS (legacy)
- **XDO cross-domain shares** (17 views to `FSA_PRD_FCCM_TECHOPS`):
  `ACTONE_ACM_ITEMS_V_CM_XDO`, `SAM_ALERTS_HIST_T_CM_XDO`,
  `SAM_PARTY_HIST_T_CM_XDO`, and 14 others. All read-only, consumed by
  Customer Monitoring (FCT domain).

**Why DataInsights does not read this domain — 4 reasons:**

1. **Separate governance:** FCAMI has its own regulatory obligations,
   escalation path, and FinCrime/AML accountability. Bolting AML triggers
   onto a sales worklist blurs that accountability.
2. **NBA/EBM contamination risk:** Using AML alert signals to rank sales
   recommendations is the wrong design — it conflates compliance and
   sales functions.
3. **PEP boundary:** Combining `PEP_PRS` scoring with political/
   geopolitical exogenous event types is specifically prohibited
   (documented in `compliance_and_industry_context.md` and GDPR/EU AI Act
   review).
4. **What IS allowed:** The `HIGH_RSK_CUST_IND` flag in
   `ENT_PRD.TIER0_PRS.PARTY` is visible (it is part of the canonical
   model). DataInsights stops at "flag exists" and does not correlate it
   with event types or use it to rank recommendations.

---

## NPDM Catalogue — C&I Non-Personal Data Model, Data Product Catalogue

**NPDM** is a registered golden source (`GSWG-SYS-00680`) that has
underpinned C&I analytics for 4+ years and supported **£260m in benefits
in 2025**. It acts as a façade layer across 20+ composed/derived data
products, all hosted in Snowflake `cid_standardized`.

**Data flow architecture:** Source Systems (Phoenix+, Loan IQ, CustDB,
RMP, …) → DLR (AWS S3, TLS 1.2+ / AES-256) → Snowflake RAW (`COPY INTO`
from S3) → NPDM (`cid_standardized`, FDM-aligned canonical layer) →
Composed/Derived Products (Iceberg tables, AES-256) → DPPS → Data
Marketplace (DMP / SMUS catalog).

Source: STRAT/2818552998 · ENDT/2832460863

**Data product catalogue** (# / Name / Type / FDM Class / Source Systems /
Use Case / DataInsights Relevance):

| # | Data Product | Type | FDM Class | Source Systems | Use Case | DataInsights Relevance |
|---|---|---|---|---|---|---|
| 1 | Customer Complaints | Composed | Communication Event | Phoenix+ | Regulatory reporting | ⭐ Service quality signal — unhappy clients need proactive attention |
| 2 | Customer Applications | Composed | Process Event | eOBAO, RMP, Loan IQ | Journey analytics | ⭐⭐ New facility intent signal — in-flight application context for ranking |
| 3 | Competitor Pricing | Composed | Product | Defaqto | Rate benchmarking | ⭐ Rate sensitivity context for TREASURY_OPPORTUNITY sizing |
| 4 | **Single View of Customer (SVoC)** | Derived | Financial Transaction | FRANK/ARC, PROSPER, SMART, RMP, FLS, GRDM, CLIMATE, NPDM | Profitability: ROE, Income/RWA | ⭐⭐⭐ Core client value context — revenue-weighted ranking input |
| 5 | **Customer Leakage** | Derived | Financial Transaction | MiMo | Competitor loss detection | ⭐⭐⭐ Attrition risk signal — urgency multiplier for any recommendation |
| 6 | Relationship Manager Hierarchy | Derived | Party (RM) | Org Model | RM segmentation | RM routing — who receives the worklist item |
| 7 | **Non Personal Loan Products** | Composed | Arrangement (Lending) | AccountDB, RMP, Lombard, LoanIQ | Lending analytics | ⭐⭐ Facility utilization, maturity, drawdown patterns |
| 8 | **Non Personal MTA Products** | Composed | Accounts (MTA) | CustDB, International | Deposit analytics | ⭐⭐ Cash flow monitoring — feeds `large_incoming_payment` baseline |
| 9 | **C&I Account Master** | Composed | Accounts, Product | AccountDB, Bankline, Potter, Lombard, RMP | Cross-product reference | ⭐⭐⭐ Baseline for all detectors — complete account universe per client |
| 10 | C&I AutoDebits & Standing Orders | Composed | Payment Instructions | CustDB, Standing Orders | Payment analytics | ⭐ Payment pattern change signal — direct debit removal = cashflow stress |
| 11 | C&I Customers NBAs, Leads & Opps | Composed | Event | CRM, eFlex | Sales pipeline | Existing opportunity de-duplication — avoid recommending open actions |
| 12 | **C&I Customer Financials** | Derived | Party (Profile) | CustDB, UKBMIS (PDL2), Lombard | Financial profile | ⭐⭐ Revenue context — denominator for offer sizing (15% of revenue rule) |
| 13 | **C&I Deposits — Flow of Funds** | Derived | Financial Transaction | Multiple sources | Money movement tracking | ⭐⭐⭐ Core cash flow pattern detector — structural revenue change signal |
| 14 | **C&I Customer Income** | Derived | Party Metric | PROSPER, NPDM, CDNA | Standardised income view | ⭐⭐⭐ Offer sizing denominator — annual revenue for all %-based sizing |
| 15 | C&I Consumer Duty | Derived | TBD | TBD | Regulatory compliance | 🟠 Boundary — never recommend to Consumer Duty flagged clients without review |
| 16 | C&I Customer in Vulnerable Situation | Derived | TBD | TBD | Vulnerability identification | 🔴 Hard stop — never target vulnerable clients for proactive NBA identification |
| 17 | C&I Customer Events | Composed | TBD | Pega | Event tracking | ⭐ CRM event correlation — recent contact context for RM |
| 18 | C&I Fincrime Analytics | Composed | TBD | TBD | Financial crime analytics | 🔴 Separate governance — do not cross-reference with NBA |
| 19 | C&I Pricing | Composed | TBD | TBD | Pricing analytics | ⭐ Rate competitiveness — context for TREASURY_OPPORTUNITY timing |
| 20 | C&I Bankline Analytics | Composed | TBD | TBD | Bankline behaviour | ⭐ Digital channel signals — online banking activity patterns |
| 21 | C&I Customer Lifetime Value | Composed | TBD | TBD | CLV modelling | ⭐⭐ Ranking weight input — high-CLV clients ranked higher for same signal |
| 22 | C&I Nexus | Composed | TBD | Pega | Pega CDH integration | Pega Customer Decision Hub integration channel for NBA delivery |

Rows in **bold** are the ones flagged with 2+ stars — i.e. the ones most
directly load-bearing for DataInsights ranking/detection logic. Rows 15
and 16 are explicit boundary/hard-stop entries analogous to the FCAMI
hard boundary above — never recommend to Consumer Duty–flagged or
vulnerable clients without human review. Row 18 (Fincrime Analytics)
reinforces the Domain 5 boundary at the data-product level too.

---

## Agent Signal Map — DataInsights Agent Knowledge Map (Domain × Signal × Hypothesis)

Each banking domain exposes endogenous signals through its physical
tables. Domain-specialist agents read these signals, correlate them with
exogenous events, and contribute evidence to a shared hypothesis. **The
final ranked recommendation is the intersection of multiple domain views
on the same client — not a single-domain alert. The more domains that
confirm a signal, the stronger the hypothesis.**

### Signal → Source Table → Exogenous Amplifier → NBA Outcome

(partial — table continues past what's been captured so far)

| Signal | Source Table (`ENT_PRD.TIER0_PRS`) | FDM Class | Exogenous Amplifier | NBA Category | Hypothesis Strength |
|---|---|---|---|---|---|
| Large incoming payment | `EVENT_FINANCIAL.FIN_EVNT_AMT` vs 90d rolling MAD | Event | Tender award (TED API) | FINANCING_NEED | ★★★★★ Strongest — direct cash + contract |
| Facility utilization >85% | `AGREEMENT_DAILY_BALANCE` vs `AGRMNT_ORIG_LIM` | Arrangement | ECB rate cut | FINANCING_NEED | ★★★★ Near limit + cheap money |
| Cash buildup >60 days | `AGRMNT_LDGR_BAL_AMT` trending upward | Account | ECB rate rise | TREASURY_OPPORTUNITY | ★★★★ Idle cash + rising rates |
| Facility maturity <90 days | `AGREEMENT.AGRMNT_CLOSE_DT` | Arrangement | None needed (calendar fact) | FINANCING_NEED | ★★★ Independent of events |
| Collateral value drop | `COLLATERAL_ITEM_VALUE.ASSET_VAL_AMT` | Party Asset | Energy/commodity shock | RISK_REVIEW | ★★★★ Falling coverage + sector stress |
| Rating downgrade | `PARTY.RSK_GRD_CD` migration | Party | Sanctions / geopolitical (OpenSanctions) | RISK_REVIEW | ★★★★★ Bank risk event + external pressure |
| FX multi-currency exposure | `AGREEMENT.AGRMNT_IBAN_NUM` multi-currency | Arrangement | Currency shock / energy price | HEDGING_NEED | ★★★ Exposure exists, event creates urgency |
| PD model migration | `LC_MODEL_DATA` / `ML_MODEL_DATA` output shift | Account Metric | Sector disruption (GDELT/EUR-Lex) | RISK_REVIEW or FINANCING_NEED | ★★★ Direction-dependent |
| Asset value appreciation | `ASSET_VALUE.ASSET_VAL_AMT` upward trend | Party Asset | CAPEX investment cycle | CAPEX_FINANCING | ★★★ Equity release opportunity |
| Revenue pattern change | `EVENT_FINANCIAL` frequency/amount trend | Event | Tender award (TED API) | FINANCING_NEED | ★★★★ Structural revenue growth |
| Fixed rate expiry | `MORTGAGE_AGREEMENT.MORT_FXED_RT_END_DT` | Arrangement | ECB rate direction | TREASURY_OPPORTUNITY or HEDGING_NEED | ★★★★ Known date + rate direction |
| Group cash pooling opportunity | `PARTY_GROUP` + `AGREEMENT_DAILY_BALANCE` | Party Group | Rate rise (ECB) | TREASURY_OPPORTUNITY | ★★★ Cross-entity optimisation |

This is the full 12-row signal table (confirmed complete per the page's
own build summary, see "Page structure summary" below).

(Domain 5 of the "Five Banking Domains" list is FCAMI — see the hard
boundary section above; all five domains are now accounted for.)

### Synthetic Training Data — what the generator needs per domain

For each signal × domain combination, the synthetic data generator must
produce four components. The physical DDL (Tab 2 / "Physical Data
Models" section above) defines exactly which columns to synthesize.

| # | Component | Description | Key Columns |
|---|---|---|---|
| 1 | Realistic baseline | 90-day rolling median per client per account type (normal behaviour) | `EVENT_FINANCIAL.FIN_EVNT_AMT` over `FIN_EVNT_PSTD_DT`, `AGRMNT_LDGR_BAL_AMT` over time |
| 2 | Injected event | Point-in-time anomaly matching the detection rule (the signal) | Single row in `EVENT_FINANCIAL` or `AGREEMENT_DAILY_BALANCE` with elevated value, specific date |
| 3 | Corresponding exogenous event | Same date window, matching sector/country code | `event_type` + `sector_code` + `country_code` in `external_events` CSV |
| 4 | Expected NBA output | Category + hypothesis + sized action for ground truth | `category`, `hypothesis`, `recommended_action`, `evidence_summary` in `protected_evaluator_only/` |

Component 4 lands in `protected_evaluator_only/` — the same
ground-truth-labels path `CLAUDE.md` says must never be read by detector
or narrator code. This confirms the generator's ground-truth output and
the detector's input are meant to be produced/consumed on opposite sides
of that boundary, consistent with the existing hard boundary rule.

### Key cross-domain SQL joins

These are the join paths DataInsights agents use to assemble a complete
client picture from `ENT_PRD.TIER0_PRS`. All use `PRTY_ID` as the
backbone.

```sql
-- === JOIN 1: Client to Transactions (Endogenous detector backbone) ===
SELECT p.PRTY_ID, p.PRTY_SGMNT_CD, p.RSK_GRD_CD,
       a.AGRMNT_ID, a.AGRMNT_TYP_CD, a.AGRMNT_ORIG_LIM,
       b.AGRMNT_LDGR_BAL_AMT, b.AGRMNT_DLY_BAL_STRT_DTTM,
       e.FIN_EVNT_AMT, e.FIN_EVNT_CURY_CD, e.FIN_EVNT_PSTD_DT
FROM   ENT_PRD.TIER0_PRS.PARTY p
  JOIN ENT_PRD.TIER0_PRS.PARTY_AGREEMENT pa   ON p.PRTY_ID = pa.PRTY_ID
  JOIN ENT_PRD.TIER0_PRS.AGREEMENT a          ON pa.AGRMNT_ID = a.AGRMNT_ID
  JOIN ENT_PRD.TIER0_PRS.AGREEMENT_DAILY_BALANCE b ON a.AGRMNT_ID = b.AGRMNT_ID
  JOIN ENT_PRD.TIER0_PRS.EVENT_FINANCIAL e    ON a.AGRMNT_ID = e.AGRMNT_ID_TRN_ACCT
WHERE  p.PRTY_SGMNT_CD IN ('SME','MID','LRGE','INST')  -- C&I segments only
  AND  b.AGRMNT_DLY_BAL_STRT_DTTM >= DATEADD(day,-90,CURRENT_DATE);

-- === JOIN 2: Client to Risk and Collateral ===
SELECT p.PRTY_ID, pa2.PRTY_ASSET_ID, pa2.PRTY_ASSET_SBTYP_CD,
       av.ASSET_VAL_AMT, av.ASSET_VAL_STRT_DTTM, av.CURY_CD,
       a.AGRMNT_ID, a.AGRMNT_ORIG_LIM
FROM   ENT_PRD.TIER0_PRS.PARTY p
  JOIN ENT_PRD.TIER0_PRS.PARTY_TO_PARTY_ASSET ppa ON p.PRTY_ID = ppa.PRTY_ID
  JOIN ENT_PRD.TIER0_PRS.PARTY_ASSET pa2          ON ppa.PRTY_ASSET_ID = pa2.PRTY_ASSET_ID
  JOIN ENT_PRD.TIER0_PRS.ASSET_VALUE av           ON pa2.PRTY_ASSET_ID = av.PRTY_ASSET_ID
  JOIN ENT_PRD.TIER0_PRS.AGREEMENT_COLLATERAL_ITEM aci ON pa2.PRTY_ASSET_ID = aci.CLTRL_ITEM_ID
  JOIN ENT_PRD.TIER0_PRS.AGREEMENT a               ON aci.AGRMNT_ID = a.AGRMNT_ID
WHERE  av.EFFECTIVE_END_DT IS NULL;  -- current valuation only

-- === JOIN 3: Client to Group Hierarchy (group treasury) ===
SELECT p.PRTY_ID, pg.PRTY_GRP_ID, pg.PRTY_GRP_NM,
       pg.PRTY_GRP_PARENT_ID,  -- traverse for group-level view
       ppg.PRTY_TO_PRTY_GRP_ROLE_CD
FROM   ENT_PRD.TIER0_PRS.PARTY p
  JOIN ENT_PRD.TIER0_PRS.PARTY_TO_PARTY_GROUP ppg ON p.PRTY_ID = ppg.PRTY_ID
  JOIN ENT_PRD.TIER0_PRS.PARTY_GROUP pg           ON ppg.PRTY_GRP_ID = pg.PRTY_GRP_ID;

-- === JOIN 4: Client Risk Rating History (downgrade detection) ===
SELECT p.PRTY_ID, p.RSK_GRD_CD AS current_grade,
       pm.PRTY_MTR_VAL AS metric_value,
       pm.EFFECTIVE_START_DT, pm.EFFECTIVE_END_DT
FROM   ENT_PRD.TIER0_PRS.PARTY p
  JOIN ENT_PRD.TIER0_PRS.PARTY_METRIC pm ON p.PRTY_ID = pm.PRTY_ID
WHERE  pm.EFFECTIVE_END_DT IS NULL  -- current metrics
ORDER  BY p.PRTY_ID;
```

### Six NBA categories — revenue map

| Category | Bank Revenue? | Mechanism | Primary Signal Source |
|---|---|---|---|
| FINANCING_NEED | ✅ Yes | Interest income + origination fees on loan/credit line/guarantee | `EVENT_FINANCIAL` amount spike, `AGRMNT` utilization, `AGRMNT_CLOSE_DT` |
| TREASURY_OPPORTUNITY | ✅ Yes | Fee/spread income from deposit or short-term investment product | `AGRMNT_LDGR_BAL_AMT` buildup, ECB rate moves |
| HEDGING_NEED | ✅ Yes | Fee income from FX or commodity hedge | Multi-currency `AGRMNT_IBAN_NUM`, `MORT_FXED_RT_END_DT`, energy shock |
| CAPEX_FINANCING | ✅ Yes | Interest income on equipment/transition financing | `ASSET_VALUE` appreciation, tender award, construction NACE codes |
| RISK_REVIEW | ❌ Defensive | Protects existing revenue via compliance/counterparty review | `RSK_GRD_CD` migration, sanctions change, `COLLATERAL_ITEM_VALUE` drop |
| ADVISORY_ONLY | ❌ Relationship | Conversation worth having, no clear product yet | Dormancy signal, general macro event, no sized action produced |

**Sizing rule reminder:** `FINANCING_NEED` and `CAPEX_FINANCING` — amount
= % of event value (tender/disaster) capped at client revenue multiple,
OR % of client annual revenue (C&I Customer Income product).
`RISK_REVIEW` and `ADVISORY_ONLY` deliberately get **no** sized number.
Every percentage is labelled "(illustrative)" inline.

This directly matches this repo's existing hypothesis-outcome convention
(category + hypothesis + sized action, never a raw score) — see the
`hypothesis_outcome_format` memory and `docs/current_state.md`.

## Page structure summary (per the source page's own completeness note)

The source page states `fdm_reference.html` is complete at 65KB, all
sections verified, with this tab structure:

- **Tab 1 — FDM Overview:** full definition of what the FDM is, why it's
  federated, the 3-tier classification (Kernel → Shared Extensions →
  Domain PDMs), a visual class diagram showing
  `PARTY → ARRANGEMENT/ACCOUNT/PARTY ASSET → EVENT/PRODUCT/CONDITION`,
  and a common-keys table (`PRTY_ID`, `AGRMNT_ID`, `EVNT_ID`, etc.) with
  child table counts pulled from real DDL.
- **Tab 2 — Physical Data Models (`ENT_PRD.TIER0_PRS`):** real DDL from
  the DLH Confluence space for all 6 domains — PARTY (66 tables,
  dual-hub architecture), AGREEMENT (75 entities including
  AGREEMENT_DAILY_BALANCE and MORTGAGE_AGREEMENT), EVENT (40 entities,
  EVENT_FINANCIAL with all 81 columns noted), PRODUCT (26 entities, all
  subtypes), PARTY_ASSET + COLLATERAL, PARTY_GROUP. Each section is
  collapsible with a DataInsights relevance callout.
- **Tab 3 — Domain Data Products:** 5 domain cards (color-coded) —
  Deposits, Lending, Treasury/TILAPI, Risk/CRADLE, and FinCrime. The
  FinCrime card spans full width with a red warning box explaining the 4
  reasons the hard boundary exists. (This is the "Five Banking Domains —
  Endogenous Signal Sources" section captured above.)
- **Tab 4 — NPDM Catalogue:** all 22 data products with type, FDM class,
  source systems, use case, and a DataInsights relevance column.
  Searchable live filter on the page.
- **Tab 5 — Agent Signal Map:** 12-row signal table mapping each
  detection rule to its source table, FDM class, exogenous amplifier, NBA
  category, and hypothesis strength in stars. Includes the 4-component
  synthetic training data spec and the 4 cross-domain SQL joins agents
  would run.

This confirms the "Five Banking Domains" section above belongs to **Tab
3: Domain Data Products** (not a separate/unnamed tab as originally
assumed), and that the Agent Signal Map's signal table (12 rows) and its
SQL-join/synthetic-data appendices are now fully captured.

---

## Not yet captured

Per the source page's own completeness statement ("Page structure
summary" section above), all 5 tabs are now believed fully captured in
this doc: FDM Overview, Physical Data Models, Domain Data Products, NPDM
Catalogue (22/22 rows), and Agent Signal Map (12/12 signal rows + 4 SQL
joins + synthetic data spec). Treat this as provisional — if further
screenshots reveal more content, that self-report was incomplete and this
section should be un-collapsed again.

Also referenced in the page footer but not expanded here: Confluence
sources DAK (FDM Overview, page 2764667073), DLH (Physical Data Models
ENT_PRD.TIER0_PRS), ENDT (NPDM High Level Design, page 2832460863), STRAT
(C&I Data Strategy, page 2818552998), FDAA (FRM Credit Risk Measures, page
2625647922), GRPTREASURY, and personal spaces (david.campbell — FCAMI Data
Mart page 2827139597, FCAMI Assumption Audit page 2826094041). Status
across the page: Draft — DLH physical models pending SME sign-off.

More domain/schema content will be appended here as further screenshots
are shared.
