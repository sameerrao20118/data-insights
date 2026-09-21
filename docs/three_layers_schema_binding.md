# Three Layers: Physical Schema → Semantic Model → Binding

**See also:** `docs/fdm_natwest_model.md` (domain-specific FDM structure), `docs/architecture.md` (pipeline integration)

This document explains the three-layer architecture that enables DataInsights to work across **any** data source — legacy CSV, FDM Snowflake, AWS Glue, or future platforms — without changing detector code.

**Critical clarification:** The engine runs **today with the legacy binding alone**. FDM is **not a runtime dependency**. Multiple independent bindings (legacy, fdm, sba) all target the same `semantic_model.yaml`. Legacy has no dependency on FDM existing or running — it's a complete, self-sufficient binding that gracefully degrades when it lacks FDM concepts. FDM-alignment is a **portability feature** for the future: when you swap profiles from `legacy_local` to `fdm_snowflake`, the same detector code runs unchanged against different physical schemas.

---

## Layer 1: Physical Schema (`config/entities.yaml` or `config/entities_fdm.yaml`)

**Location:** CSV files on disk or Snowflake tables

**What it describes:** The actual structure of data as it exists in its native system.

### Example: Legacy CSV

```yaml
# config/entities.yaml
entities:
  accounts:
    physical_table: accounts                    # CSV filename
    grain: one row per bank account
    primary_key: [account_id]
    required_columns:
      account_id: {type: string, nullable: false}
      client_id: {type: string, nullable: false}    # Real column in CSV
      account_type: {type: string, nullable: false}
      currency: {type: string}
    optional_columns:
      credit_limit: {type: decimal}
```

**Reality:** This is what the CSV actually has.
```
File: data_generator/output/accounts.csv
account_id,client_id,account_type,currency
AC-001,CLI-123,current,EUR
```

### Example: FDM Snowflake

```yaml
# config/entities_fdm.yaml
entities:
  AGREEMENT:
    physical_table: ENT_PRD.TIER0_PRS.AGREEMENT
    grain: one row per facility/agreement
    primary_key: [AGRMNT_ID]
    required_columns:
      AGRMNT_ID: {type: number}
      AGRMNT_ORIG_LIM: {type: number}
      AGRMNT_CLOSE_DT: {type: date}
      AGRMNT_TYP_CD: {type: varchar}
```

**Reality:** This is what the Snowflake table actually has.

---

## Layer 2: Semantic Model (`config/semantic_model.yaml`)

**Location:** In the codebase (not in any database)

**What it describes:** The abstract, canonical concepts that all detectors agree on.

**Key principle:** This layer is **schema-agnostic** — it doesn't care whether data comes from CSV or Snowflake. It just defines the business concepts.

### Example

```yaml
# config/semantic_model.yaml
concepts:
  Account:
    kind: entity
    fields:
      account_id: string
      party_id: string              # NOT "client_id" — canonical name
      product_code: string          # NOT "account_type" — canonical name
      opening_balance: decimal
      closing_balance: decimal
      currency: string
    description: "One bank account"
```

**Key insight:** The semantic model **doesn't have a `client_id` column**. It has `party_id` (the canonical banking term, following FDM's Party Kernel Class). This is intentional.

---

## Layer 3: Binding (`config/bindings/legacy.yaml` or `config/bindings/fdm.yaml`)

**Location:** Mapping configuration between Layers 1 and 2

**What it does:** Translates physical columns → semantic fields. It's a **bridge**.

### Example: Legacy Binding

```yaml
# config/bindings/legacy.yaml
schema: legacy
contract_ref: config/entities.yaml

concepts:
  Account:
    entity: accounts                # Points to Layer 1's "accounts" entity
    fields:
      account_id: account_id        # CSV column `account_id` → canonical field `account_id`
      party_id: client_id           # ⭐ CSV column `client_id` → canonical field `party_id` (RENAME!)
      product_code: account_type    # CSV column `account_type` → canonical field `product_code`
      currency: currency
```

**What this means:**
- When a detector asks for `Account.party_id`, the binding intercepts it
- The binding says: "Look for `party_id` in Layer 2 (semantic) — but in Layer 1 (physical), that's called `client_id`"
- The binding reads the CSV column `client_id` and returns it as `party_id`

### Example: FDM Binding

```yaml
# config/bindings/fdm.yaml
schema: fdm
contract_ref: config/entities_fdm.yaml

concepts:
  Account:
    entity: AGREEMENT              # Points to Layer 1's Snowflake table
    bitemporal:
      valid_from: EFFECTIVE_START_DT
      valid_to: EFFECTIVE_END_DT
    fields:
      account_id: AGRMNT_ID       # Snowflake column → canonical field
      party_id: PRTY_ID            # (joined from PARTY_AGREEMENT table)
      product_code: AGRMNT_TYP_CD
```

**What this means:**
- Same semantic model (Layer 2) — no detector changes
- Different physical location (Layer 1) — Snowflake instead of CSV
- Binding handles the translation

---

## How They Work Together: Runtime Example

### Scenario: Detector Finds Account with Low Balance

**Step 1: Detector Code (Uses Layer 2 only)**
```python
# detectors/deposits.py
def detect_low_balance(accounts: DataFrame) -> DataFrame:
    # Detector NEVER knows about CSV column names
    # It only uses canonical semantic names
    return accounts[
        accounts['party_id'] == 'CLI-123'   # Canonical name
    ][['account_id', 'closing_balance']]
```

**Step 2: Runtime Resolves the Binding (Layer 3)**
```python
# When the detector is about to run:
profile = load_profile('legacy_local')  # Decides to use legacy binding
binding = load_binding('config/bindings/legacy.yaml')
canonical_source = CanonicalSource(binding=binding)

# Detector asks for 'party_id'
accounts = canonical_source.get_accounts()
```

**Step 3: Binding Translates Physical → Semantic (Layer 1 → Layer 2)**

| Layer | What the binding sees | What it does |
|---|---|---|
| Layer 1 (Physical) | CSV has column `client_id` | Read CSV column |
| Layer 3 (Binding) | Binding says `party_id: client_id` | Intercept request |
| Layer 2 (Semantic) | Detector asks for `party_id` | Return as `party_id` |

**Step 4: Detector Receives Data (Uses Layer 2 semantics)**
```python
# accounts DataFrame now has canonical columns
# accounts['party_id'] == 'CLI-123' works
# No binding, no CSV column names visible
```

---

## Why This Matters: Three Scenarios

### Scenario A: Switching from CSV to Snowflake (Same Detector)

**Before:**
```
config/profiles/legacy_local.yaml
├─ source: offline_local_flat (CSV)
└─ binding: legacy
```

**After:**
```
config/profiles/fdm_snowflake.yaml
├─ source: snowflake (ENT_PRD.TIER0_PRS)
└─ binding: fdm
```

**Detector code:** UNCHANGED. Same `Account.party_id` usage.

### Scenario B: Adding a Revenue-Driving Column

1. Add to Layer 1 (Physical Schema):
   ```yaml
   # config/entities.yaml
   accounts:
     required_columns:
       fee_basis_points: {type: decimal, nullable: false}
   ```

2. Add to Layer 2 (Semantic Model):
   ```yaml
   # config/semantic_model.yaml
   concepts:
     Account:
       fields:
         fee_basis_points: decimal
   ```

3. Add to Layer 3 (Binding):
   ```yaml
   # config/bindings/legacy.yaml
   concepts:
     Account:
       fields:
         fee_basis_points: fee_basis_points  # Direct map
   ```

4. Detector now has access:
   ```python
   def detect_fee_opportunity(accounts):
       return accounts[accounts['fee_basis_points'] > 50]
   ```

### Scenario C: Adding a New Data Source (AWS Glue)

1. Create Layer 1 (Physical):
   ```yaml
   # config/entities_glue.yaml
   entities:
     party_agreements:  # Different name than Snowflake
       physical_table: s3://bucket/party_agreements/
       required_columns:
         party_id: {type: string}
         agreement_id: {type: string}
         fee_bps: {type: decimal}
   ```

2. Reuse Layer 2 (Semantic) — unchanged

3. Create Layer 3 (Binding):
   ```yaml
   # config/bindings/glue.yaml
   schema: glue
   contract_ref: config/entities_glue.yaml
   
   concepts:
     Account:
       entity: party_agreements
       fields:
         party_id: party_id
         account_id: agreement_id
         fee_basis_points: fee_bps  # Glue uses "fee_bps", bind to canonical "fee_basis_points"
   ```

4. Detector works unchanged with AWS data.

---

## The Key Insight

| Layer | Purpose | Audience | Stability |
|---|---|---|---|
| **Layer 1: Physical** | Describes reality as-is | DataSource adapters, DBAs | Changes when data structure changes |
| **Layer 2: Semantic** | Defines canonical concepts | Detectors, business logic | Stable; central banking definitions (FDM) |
| **Layer 3: Binding** | Bridges physical ↔ semantic | Adapter runtime | Grows as new sources added; one binding per schema |

**In practice:**
- Detector code is written **once** using Layer 2 (semantic)
- Multiple Layer 3 (bindings) can exist for different sources
- All detectors work with all sources automatically
- No detector rewrites, no source-specific code branches

---

## References

- **FDM Kernel Classes:** `docs/fdm_natwest_model.md` (Party, Arrangement, Account, Event, etc.)
- **Pipeline integration:** `docs/architecture.md` Section: "Semantic layer"
- **Binding implementation:** `datainsights/semantic/CanonicalSource`
- **Test coverage:** `tests/test_no_source_specific_coupling.py` (ensures no detector uses physical column names)

