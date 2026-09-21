# Schema & Binding Guide: Quick Reference

**TL;DR:** This is your index to understanding how DataInsights maps physical data → canonical concepts → detector logic.

**Important:** The engine runs **today with the legacy binding** — FDM is **not a runtime dependency**. Legacy is a complete, self-sufficient binding. FDM-alignment is a **portability feature**: it lets you swap to `fdm_snowflake` later without changing detector code. Legacy has no dependency on FDM existing.

---

## The Three Layers (Read First)

**Start here:** `docs/three_layers_schema_binding.md`

| Layer | File | Purpose |
|---|---|---|
| **Layer 1: Physical** | `config/entities.yaml` | Describes actual data (CSV columns, Snowflake tables) |
| **Layer 2: Semantic** | `config/semantic_model.yaml` | Defines canonical business concepts (FDM-aligned) |
| **Layer 3: Binding** | `config/bindings/legacy.yaml` | Translates physical → semantic (CSV `client_id` → canonical `party_id`) |

**Key insight:** Detector code uses Layer 2 only. Multiple Layer 3 bindings exist for different sources (legacy CSV, FDM Snowflake, AWS Glue) — same detector works with all.

---

## FDM Model at NatWest

**Read next:** `docs/fdm_natwest_model.md`

| Concept | What It Is | Examples |
|---|---|---|
| **FDM Kernel Classes** | 8 canonical banking entities (Party, Arrangement, Account, Event, Product, Condition, Locator, Party Asset) | Every banking system needs these concepts |
| **Five Domains** | Business-specific views on the same client | Deposits, Lending, Treasury, Risk, FinCrime |
| **Endogenous Signals** | Internal signals from each domain | Large payment, facility utilization, rating downgrade |
| **NBA Categories** | Recommendation types that drive revenue | FINANCING_NEED, TREASURY_OPPORTUNITY, RISK_REVIEW, etc. |

**Your legacy schema:** Simplified, proof-of-concept version of FDM that proves detectors are schema-agnostic.

---

## Adding New Columns & Dealing with Gaps

**Read when needed:** `docs/handling_domain_gaps.md`

| Situation | What to Do |
|---|---|
| **Column exists in CSV/Snowflake, not contracted** | Add to `entities.yaml` + `semantic_model.yaml` + `bindings/legacy.yaml` → Detector ready (1 commit) |
| **Column doesn't exist, but FDM has it** | Generate in `data_generator.py` → then do step 1 (3 commits) |
| **Column doesn't exist, FDM doesn't have it** | Add to data generator as external enrichment → document gap in `docs/gap_analysis.md` (2 commits + docs) |
| **Hard boundary (FinCrime)** | Never read; separate governance (by design) |

---

## The Flow (For a New Detector)

```
1. Identify what column you need
         ↓
2. Check: Does it exist in data?
   No? → Add to data generator
         ↓
3. Check: Is it contracted in entities.yaml?
   No? → Add to contract + semantic model + binding
         ↓
4. Detector uses canonical field name (from semantic model)
   (e.g., accounts['party_id'], not accounts['client_id'])
         ↓
5. Binding intercepts at runtime
   Binding: "party_id in semantic = client_id in CSV"
         ↓
6. Detector gets the right data with right name
   Works with CSV, FDM Snowflake, AWS Glue — no code changes
```

---

## File Locations

| Component | File Path | Purpose |
|---|---|---|
| **Physical schemas** | `config/entities.yaml` | Legacy CSV structure |
| | `config/entities_fdm.yaml` | FDM Snowflake structure (not run) |
| **Semantic model** | `config/semantic_model.yaml` | Canonical concepts (FDM-aligned) |
| **Bindings** | `config/bindings/legacy.yaml` | CSV → semantic translation |
| | `config/bindings/fdm.yaml` | Snowflake → semantic translation (not run) |
| **Profiles** | `config/profiles/legacy_local.yaml` | Runtime config: CSV source + legacy binding |
| | `config/profiles/fdm_snowflake.yaml` | Runtime config: Snowflake source + fdm binding (not run) |
| **Detector code** | `detection_engine/*.py` | Uses canonical field names only |
| **Data generator** | `data_generator/generate_data.py` | Creates synthetic CSVs |
| **Concepts** | `datainsights/semantic/concepts.py` | Canonical concept definitions |
| **Source** | `datainsights/semantic/CanonicalSource` | Applies binding at runtime |

---

## Example: Adding a Revenue Column

Scenario: You want to add `fee_basis_points` (fee charged on transactions) to detect cross-sell opportunities.

### Step 1: Verify it exists in CSV
```bash
head -1 data_generator/output/transactions.csv | grep fee_basis_points
# Output: ...,fee_basis_points,...
```

### Step 2: Contract it
```yaml
# config/entities.yaml
transactions:
  required_columns:
    fee_basis_points: {type: decimal, nullable: false, revenue_driver: true}
```

### Step 3: Add to semantic model
```yaml
# config/semantic_model.yaml
concepts:
  Transaction:
    fields:
      fee_basis_points: decimal
```

### Step 4: Add to binding
```yaml
# config/bindings/legacy.yaml
concepts:
  Transaction:
    entity: transactions
    fields:
      fee_basis_points: fee_basis_points  # CSV → semantic (direct map)
```

### Step 5: Write detector
```python
# detectors/cross_sell.py
def detect_fee_opportunity(transactions: DataFrame) -> DataFrame:
    return transactions[
        transactions['fee_basis_points'] > 50  # Canonical name (from binding)
    ]
```

### Step 6: Test
```bash
python -m datainsights.pipeline --profile legacy_local --validate-only
```

---

## Validation Checklist

Before submitting a PR:

- [ ] **New column added?** Is it in `entities.yaml`?
- [ ] **Semantic model updated?** Does it define the canonical field?
- [ ] **Binding updated?** Does it map physical → semantic?
- [ ] **Detector uses canonical names only?** No column renames at detector level
- [ ] **Adapter validates?** Run: `python -m datainsights.sources.csv_source --validate-schema config/entities.yaml`
- [ ] **Tests pass?** Especially `test_no_source_specific_coupling.py` (ensures no detector uses physical column names)

---

## When Things Break

| Error | Likely Cause | Fix |
|---|---|---|
| `KeyError: 'party_id' not found` | Detector uses canonical name, binding missing that mapping | Add to `config/bindings/legacy.yaml` |
| `Column 'client_id' not in contract` | Physical column not declared in `entities.yaml` | Add to `required_columns` or `optional_columns` |
| `Concept 'Account' has no field 'party_id'` | Semantic model missing the field | Add to `config/semantic_model.yaml` |
| `CanonicalSource refuses to load` | Binding references non-existent entity | Check entity name in `entities.yaml` |

---

## Further Reading

- **Mechanics:** `docs/three_layers_schema_binding.md`
- **FDM context:** `docs/fdm_natwest_model.md`, `docs/fdm_reference.md`
- **Adding columns:** `docs/handling_domain_gaps.md`
- **Gap tracking:** `docs/gap_analysis.md`
- **Pipeline integration:** `docs/architecture.md` (Section: "Semantic layer")
- **Code:** `datainsights/semantic/CanonicalSource`, `datainsights/config.py`, `detection_engine/*.py`

---

## Quick Links

- **Live binding:** `config/bindings/legacy.yaml`
- **Semantic model:** `config/semantic_model.yaml`
- **Entity contract:** `config/entities.yaml`
- **Profile (runtime config):** `config/profiles/legacy_local.yaml`
- **Detector example:** `detection_engine/deposits_detector.py`
- **Tests:** `tests/test_no_source_specific_coupling.py`

