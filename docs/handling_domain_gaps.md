# Handling Domain Gaps: Strategy for Partially Aligned Domains

**See also:** `docs/fdm_natwest_model.md` (domain overview), `docs/three_layers_schema_binding.md` (schema mechanics), `CLAUDE.md` (hard boundaries)

When a detector needs a column or concept that doesn't exist in your current `config/entities.yaml`, you have several options. This document outlines the strategy for each case.

**Note:** This is a *choice*, not a blocker. The legacy engine runs standalone today. These strategies are for **expanding coverage** (adding more detectors) and **future FDM portability** (swapping to Snowflake binding later), not for making the engine work in the first place.

---

## The Problem

Your legacy schema contracts **only 3 entities** (transactions, accounts, balances) and **covers Deposits + basic Lending**. But FDM has **66+ tables across 5 domains**:

| Domain | Your Status | Gap | Impact |
|---|---|---|---|
| **Deposits** | ✅ Full | None | Detectors ready |
| **Lending** | ⚠️ 50% | No collateral, mortgages, risk ratings | Facility maturity works; collateral coverage blocked |
| **Treasury** | ❌ 0% | No group hierarchy, FX, intraday | Multi-currency, group pooling blocked |
| **Risk** | ⚠️ 10% | `HIGH_RSK_CUST_IND` flag only | Rating downgrade, PD migration blocked |
| **FinCrime** | 🚫 Intentional hard boundary | FCAMI domain separate | By design; never read |

**Question:** When a detector needs `COLLATERAL_ITEM_VALUE` (not in your schema), what do you do?

---

## Decision Tree

```
Does the column exist in your physical data?
  │
  ├─ YES (in CSV or Snowflake)
  │   └─ Is it already contracted in entities.yaml?
  │       ├─ YES → Use it; add to detector
  │       └─ NO  → Go to: "Add to Entity Contract" (Section 1)
  │
  └─ NO (column doesn't exist)
      └─ Is there an FDM equivalent?
          ├─ YES → Go to: "Align with FDM" (Section 2)
          └─ NO  → Go to: "FDM Gap" (Section 3)
```

---

## Section 1: Add to Entity Contract (Column Exists, Not Contracted)

**Scenario:** The column already exists in your CSV or Snowflake (`clients.csv` has `risk_rating`), but `config/entities.yaml` doesn't mention it.

**Action:** Contract it.

### Step 1a: Update Entity Contract

Edit `config/entities.yaml`:

```yaml
clients:
  physical_table: clients          # New entity (or add to existing)
  grain: one row per client
  primary_key: [client_id]
  required_columns:
    client_id: {type: string, nullable: false}
    risk_rating: {type: string, nullable: false}     # ← New column
    high_risk_flag: {type: bool, nullable: false}
  optional_columns:
    sector_code: {type: string, fallback_if_missing: "unknown"}
    country_code: {type: string, fallback_if_missing: "unknown"}
```

**Note:** Check the CSV header to get the exact column name and type:
```bash
head -1 data_generator/output/clients.csv
# Output: client_id,risk_rating,high_risk_flag,sector_code,...
```

### Step 1b: Update Semantic Model

Edit `config/semantic_model.yaml`:

```yaml
concepts:
  Party:  # FDM Kernel Class name
    kind: entity
    fields:
      party_id: string
      risk_rating: string           # ← New canonical field
      high_risk_flag: bool
      sector_code: string
      country_code: string
```

### Step 1c: Update Binding

Edit `config/bindings/legacy.yaml`:

```yaml
concepts:
  Party:
    entity: clients                 # Points to the clients table from Step 1a
    fields:
      party_id: client_id           # CSV client_id → canonical party_id
      risk_rating: risk_rating      # Direct map (same name)
      high_risk_flag: high_risk_flag
```

For FDM, if the column has a different Snowflake name:

```yaml
# config/bindings/fdm.yaml
concepts:
  Party:
    entity: PARTY
    fields:
      party_id: PRTY_ID
      risk_rating: RSK_GRD_CD       # Snowflake uses RSK_GRD_CD
```

### Step 1d: Validate

Run the adapter to check the column exists and has the right type:

```bash
python -m datainsights.sources.csv_source \
  --csv-path data_generator/output/clients.csv \
  --validate-schema config/entities.yaml
```

**Expected:** No errors. If the column is missing or has the wrong type, the adapter rejects with an actionable message.

### Step 1e: Write Detector

Now the detector can use the new field:

```python
# detectors/risk.py
def detect_rating_downgrade(parties: DataFrame) -> DataFrame:
    return parties[
        parties['risk_rating'] < 'B'  # Canonical field name (from binding)
    ][['party_id', 'risk_rating']]
```

**Result:** ✅ Detector ready. New column is now part of the contract and schema-agnostic (works with FDM too).

---

## Section 2: Align with FDM (Column Doesn't Exist, But FDM Has It)

**Scenario:** Your detector needs collateral coverage information, but your CSV doesn't have `COLLATERAL_ITEM_VALUE`. However, **FDM has it** in `PARTY_ASSET.ASSET_VALUE`.

**Action:** Add to data generator, then contract.

### Step 2a: Identify FDM Equivalent

Consult `docs/fdm_reference.md`:

```yaml
# From fdm_reference.md, PARTY_ASSET Domain section:
PARTY_ASSET:
  hub: PARTY_ASSET (PRTY_ASSET_ID, 8 children)

ASSET_VALUE:
  columns:
    - PRTY_ASSET_ID (FK -> PARTY_ASSET)
    - ASSET_VAL_AMT              # ← This is what you need
    - ASSET_VAL_STRT_DTTM        # When valuation started
    - ASSET_VAL_END_DTTM         # When it ended
    - ASSET_VALUTN_MTHD_CD       # Valuation method (market, book, etc.)
    - CURY_CD                    # Currency
```

### Step 2b: Generate Synthetic Data

Edit `data_generator/generate_data.py` to produce synthetic `party_asset.csv` and `asset_value.csv`:

```python
# data_generator/generate_data.py
def generate_party_assets(clients, num_per_client=2):
    """Generate collateral assets per client (simplified PARTY_ASSET)."""
    rows = []
    for client_id in clients['client_id']:
        for i in range(num_per_client):
            rows.append({
                'party_asset_id': f'PA-{client_id}-{i}',
                'client_id': client_id,
                'asset_type': random.choice(['property', 'equipment', 'securities']),
                'description': f'{asset_type} for {client_id}',
            })
    return pd.DataFrame(rows)

def generate_asset_values(party_assets, lookback_days=90):
    """Generate valuation history (ASSET_VALUE analog)."""
    rows = []
    for idx, asset in party_assets.iterrows():
        for days_ago in range(0, lookback_days, 7):  # Weekly valuations
            rows.append({
                'party_asset_id': asset['party_asset_id'],
                'asset_value': base_value * (1 + random.uniform(-0.05, 0.05)),  # ±5% variance
                'valuation_date': TODAY - timedelta(days=days_ago),
            })
    return pd.DataFrame(rows)
```

**Output:**
```
data_generator/output/party_assets.csv
party_asset_id,client_id,asset_type,description
PA-CLI-001-0,CLI-001,property,"London HQ"
PA-CLI-001-1,CLI-001,equipment,"Machinery"

data_generator/output/asset_values.csv
party_asset_id,asset_value,valuation_date
PA-CLI-001-0,500000,2026-09-20
PA-CLI-001-0,480000,2026-09-13
```

### Step 2c: Contract the New Entities

Edit `config/entities.yaml`:

```yaml
party_assets:
  physical_table: party_assets
  grain: one row per collateral asset
  primary_key: [party_asset_id]
  required_columns:
    party_asset_id: {type: string, nullable: false}
    client_id: {type: string, nullable: false}
    asset_type: {type: string, nullable: false}
    description: {type: string}

asset_values:
  physical_table: asset_values
  grain: one row per asset per valuation date
  primary_key: [party_asset_id, valuation_date]
  required_columns:
    party_asset_id: {type: string, nullable: false}
    asset_value: {type: decimal, nullable: false}
    valuation_date: {type: date, nullable: false}
```

### Step 2d: Update Semantic Model

```yaml
# config/semantic_model.yaml
concepts:
  PartyAsset:
    kind: entity
    fields:
      asset_id: string
      party_id: string
      asset_type: string
      current_value: decimal        # Latest valuation
      valuation_date: date
```

### Step 2e: Update Binding

```yaml
# config/bindings/legacy.yaml
concepts:
  PartyAsset:
    entity: party_assets
    fields:
      asset_id: party_asset_id
      party_id: client_id           # Rename
      asset_type: asset_type
    joins:
      - entity: asset_values
        on: party_asset_id
        fields:
          current_value: asset_value  # Latest value
          valuation_date: valuation_date
```

### Step 2f: Write Detector

```python
def detect_collateral_coverage_drop(party_assets: DataFrame) -> DataFrame:
    # Compare current valuation vs. facility limit
    # (joined with accounts earlier in the pipeline)
    return party_assets[
        party_assets['current_value'] < party_assets['facility_limit'] * 0.8
    ]
```

**Result:** ✅ New domain aligned with FDM. Detector ready.

---

## Section 3: FDM Gap (Column Doesn't Exist, FDM Doesn't Have It Either)

**Scenario:** Your detector needs a revenue-driving column that **neither your CSV nor FDM has** (e.g., `expected_expansion_probability` computed by an external AI vendor).

**Action:** Add to data generator, document as "external enrichment."

### Step 3a: Evaluate the Gap

**Questions:**
1. Is this truly a new column, or is it derived from existing FDM concepts?
2. Should it be in FDM long-term (escalate to Data Models Working Group)?
3. Or is it a DataInsights-specific enhancement (no FDM alignment needed)?

**Example responses:**
- **Derived from existing:** `expected_expansion_probability` = f(sector_code, revenue_growth, collateral_value) → Compute in detector, don't contract
- **New FDM concept:** `customer_sustainability_score` = external vendor input → Add to generator with metadata noting it's external
- **DataInsights-specific:** `detector_confidence_score` → Never contract; this is output, not input data

### Step 3b: Add to Data Generator (If External Enrichment)

```python
# data_generator/generate_data.py
def enrich_with_external_vendor_data(clients):
    """Add external AI vendor's expansion score."""
    vendors_api = VendorAIClient(api_key=os.getenv('VENDOR_API_KEY'))
    rows = []
    for client_id in clients['client_id']:
        score = vendors_api.get_expansion_score(client_id)
        rows.append({
            'client_id': client_id,
            'expansion_score': score,
            'vendor': 'ExternalAI',
            'as_of_date': TODAY,
        })
    return pd.DataFrame(rows)
```

### Step 3c: Contract as "External Enrichment"

```yaml
# config/entities.yaml
client_enrichment:
  physical_table: client_enrichment
  grain: one row per client per refresh (daily)
  primary_key: [client_id, as_of_date]
  required_columns:
    client_id: {type: string, nullable: false}
    expansion_score: {type: decimal, nullable: false}
  metadata:
    source: external_vendor
    vendor: ExternalAI
    update_frequency: daily
    note: "Not part of FDM; external enrichment only"
```

### Step 3d: Update Binding

```yaml
concepts:
  Party:
    entity: clients
    fields:
      # existing fields...
    enrichments:
      - entity: client_enrichment
        on: client_id
        fields:
          expansion_score: expansion_score
```

### Step 3e: Document as "External" in Code

```python
def detect_expansion_opportunity(parties: DataFrame) -> DataFrame:
    """Uses external vendor score (ExternalAI) to identify growth clients."""
    # Note: expansion_score is external enrichment, not FDM canonical
    return parties[parties['expansion_score'] > 0.7]
```

### Step 3f: Plan FDM Alignment (Future)

Document in `docs/gap_analysis.md`:
```markdown
**Gap:** `expansion_score` (external AI vendor)
**Status:** Working as external enrichment
**FDM alignment:** Escalate to DMWG if it becomes core to NBA pipeline
**Owners:** DataInsights + Vendor management
**Next step:** After Q4 evaluation, decide: (a) formalize in FDM, (b) keep external, or (c) retire
```

**Result:** ✅ Detector ready. Gap documented for future FDM evolution.

---

## Summary Table: When to Do What

| Scenario | Column Exists in Data? | FDM Has It? | Action | Result |
|---|---|---|---|---|
| **1. Exists, not contracted** | ✅ Yes | - | Add to contract (Section 1) | ✅ Detector ready |
| **2. Doesn't exist, FDM has it** | ❌ No | ✅ Yes | Add to generator (Section 2) | ✅ FDM-aligned |
| **3. Doesn't exist, FDM gap** | ❌ No | ❌ No | Document as external (Section 3) | ✅ Working, gap tracked |
| **4. Hard boundary (FinCrime)** | ✅ Yes | ✅ Yes | 🚫 Never read | ✅ By design |

---

## Real Examples

### Example 1: Adding Risk Ratings (Section 1)

**Column:** `RSK_GRD_CD` (risk grade) already in `clients.csv`, not contracted
- **Action:** Add to `config/entities.yaml` + binding
- **Detector:** `detect_rating_downgrade()` now works
- **Timeline:** 1 commit

### Example 2: Adding Collateral Coverage (Section 2)

**Column:** `ASSET_VAL_AMT` (collateral value) not in CSV, but FDM has it
- **Action:** Generate synthetic data + contract + binding
- **Detector:** `detect_collateral_coverage_drop()` now works
- **Timeline:** 2-3 commits (generator, contract, detector)

### Example 3: Adding External AI Score (Section 3)

**Column:** `expansion_score` from external vendor, not in FDM
- **Action:** Generate from API + contract as "enrichment" + document gap
- **Detector:** `detect_expansion_opportunity()` now works
- **Timeline:** 2 commits (generator, contract) + gap doc

---

## Checklist: Before Writing a Detector

- [ ] **Column exists?** If no, consult decision tree above
- [ ] **Contracted in entities.yaml?** If no, do Section 1 or 2
- [ ] **In semantic_model.yaml?** If no, add concept + fields
- [ ] **In binding?** If no, add mapping (CSV col → canonical field)
- [ ] **Binding tested?** Run adapter validation
- [ ] **Detector uses canonical names only?** No physical column names
- [ ] **FDM gap documented?** If external enrichment, add to gap_analysis.md

---

## References

- **FDM domains:** `docs/fdm_natwest_model.md`
- **FDM DDL:** `docs/fdm_reference.md`
- **Binding mechanics:** `docs/three_layers_schema_binding.md`
- **Gap analysis:** `docs/gap_analysis.md`
- **Hard boundaries:** `CLAUDE.md`

