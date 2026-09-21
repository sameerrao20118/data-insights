# DataInsights User Guide: Getting Started (Step-by-Step)

**This is your starting point.** Read this first. It orchestrates all other documentation in the right order.

---

## Before You Start: Prerequisites

### System Requirements
- Python 3.9+
- Git
- Local Ollama (for LLM narration, optional for first run)
- DuckDB (in-memory, no setup needed)
- ~2GB disk for synthetic data

### Software Dependencies
```bash
pip install -r requirements.txt
# Includes: pandas, duckdb, pyyaml, scikit-learn, flask
```

### Data Requirements
- CSV files with banking data (accounts, transactions, balances)
- OR: Run `python -m data_generator.generate_data` to create synthetic test data
- At least 100 rows of transaction history per client for meaningful signals

---

## The User's Journey: Three Stages

```
Stage 1: UNDERSTAND (Read docs in order)
  └─ Conceptual knowledge of schema → binding → detector flow

Stage 2: CONFIGURE (Edit YAML files)
  └─ Define your data structure and business rules

Stage 3: RUN (Execute pipeline)
  └─ See detectors work, generate recommendations
```

---

## STAGE 1: UNDERSTAND THE SYSTEM (Read These Docs in Order)

### 1.1: **Five Minutes** — Start Here: Architecture Overview
**Document:** `docs/architecture.md`

What to learn:
- What problem does DataInsights solve?
- High-level pipeline: Source → Binding → Detector → Ranking → Narrative → Dashboard
- Key components and their responsibilities

**Key takeaway:** "It reads data, detects patterns, combines signals, narrates recommendations."

---

### 1.2: **10 Minutes** — The Three-Layer Schema Model
**Document:** `docs/three_layers_schema_binding.md`

What to learn:
- **Layer 1 (Physical):** Your actual CSV columns (`client_id`, `account_type`)
- **Layer 2 (Semantic):** Canonical names detectors use (`party_id`, `product_code`)
- **Layer 3 (Binding):** Translation rules (`client_id` → `party_id`)

**Why this matters:** Your detector code never changes when you switch data sources. The binding handles translation.

**Key takeaway:** "Physical schema is data-specific. Semantic schema is universal. Binding bridges them."

---

### 1.3: **10 Minutes** — How Your Data Flows Through the System
**Document:** `docs/data_flow_runtime.md`

What to learn:
- CSV file → Adapter validation → Binding transformation → Detector logic → Signal Bus → Recommendation → Dashboard
- Real example: How one client's CSV records become a dashboard recommendation
- Where things can fail and why

**Key takeaway:** "Data flows through 9 layers. Each layer is testable independently."

---

### 1.4: **10 Minutes** — Dashboard Viewer's Perspective
**Document:** `docs/README_FOR_DASHBOARD_VIEWERS.md`

What to learn:
- What the dashboard shows (worklist of clients + recommendations)
- Where each recommendation came from (CSV data + detector logic)
- How "Explore a source" tool works for debugging

**Key takeaway:** "The dashboard is deterministic output of the pipeline. No black box."

---

### 1.5: **15 Minutes** — Signal to Category: How Clients Get Shortlisted
**Document:** `docs/signal_to_category_logic.md`

What to learn:
- Each detector signal maps to a default category (FINANCING_NEED, TREASURY_OPPORTUNITY, etc.)
- Multiple signals for same client are combined using rules (e.g., "balance building + facility drawn = growth outrunning working capital")
- How YAML rules (domains_*.yaml) control category assignment

**Key takeaway:** "Signal → Category mapping is 100% in YAML config. No Python logic."

---

### 1.6: **15 Minutes** — YAML Rules Execution Against Data
**Document:** `docs/yaml_rules_executed_against_data.md`

What to learn:
- YAML is loaded into memory at startup as a Python dict
- Detector runs against CSV, produces Signal
- Assembler looks up Signal.signal_type in YAML-loaded dict
- Category assigned from YAML

**Key takeaway:** "YAML instructs, Python executes. The bridge is signal_type string."

---

### 1.7: **10 Minutes** — FDM Model Alignment
**Document:** `docs/fdm_natwest_model.md`

What to learn:
- NatWest's FDM is a semantic standard (8 Kernel Classes: Party, Arrangement, Account, Event, etc.)
- Your legacy schema targets same canonical concepts
- Why FDM-alignment matters for production (portability across schemas)

**Key takeaway:** "FDM is design inspiration, not runtime dependency. Legacy runs standalone."

---

### 1.8: **10 Minutes** — ML Status: What's Automatic vs. What Requires Setup
**Document:** `docs/ML_STATUS_AND_STRATEGY.md`

What to learn:
- System runs 100% deterministic today (no ML in production)
- IsolationForest baseline exists but is opt-in, not active
- ML unlocks when ground-truth RM feedback exists
- Why deterministic-first is safer than black-box

**Key takeaway:** "You don't need ML to start. Detectors are auditable rules."

---

### 1.9: **20 Minutes** — All 11 Detectors & Signal Combinations
**Document:** `docs/all_detectors_and_signals.md`

What to learn:
- Complete list of 11 detectors (Deposits, Lending, Risk domains)
- How each aggregates multiple CSV records into one signal
- Example: 30 daily balance records → 1 `cash_buildup` signal
- Combination rules: when 2+ signals together override individual categories

**Key takeaway:** "9 detectors are wired. Edge cases exist but need RM feedback to validate."

---

## STAGE 2: CONFIGURE YOUR DATA (Edit 5 YAML Files)

### 2.1: Define Your Data Structure
**File to edit:** `config/entities.yaml`

```yaml
entities:
  accounts:
    physical_table: accounts              # Your CSV filename
    grain: one row per bank account
    primary_key: [account_id]
    required_columns:
      account_id: {type: string, nullable: false}
      client_id: {type: string, nullable: false}
      account_type: {type: string}
      # Add your own columns here
```

**Steps:**
1. Open `config/entities.yaml`
2. For each CSV file you have, create an entity block
3. List all columns with types (string, decimal, date, bool)
4. Mark required vs. optional
5. Validate: `python -m datainsights.sources.csv_source --validate-schema config/entities.yaml`

**Document:** `docs/handling_domain_gaps.md` (Section 1: "Add to Entity Contract")

---

### 2.2: Define Canonical Field Names
**File to edit:** `config/semantic_model.yaml`

```yaml
concepts:
  Account:
    kind: entity
    fields:
      account_id: string
      party_id: string          # Canonical name (not account_id from CSV)
      product_code: string      # Canonical name (not account_type from CSV)
```

**Steps:**
1. Open `config/semantic_model.yaml`
2. For each entity in entities.yaml, create a concept
3. Define canonical field names (these stay the same across all schemas)
4. Detectors will use these canonical names

**Document:** `docs/three_layers_schema_binding.md` (Layer 2)

---

### 2.3: Map Physical Columns to Canonical Fields
**File to edit:** `config/bindings/your_schema.yaml`

```yaml
schema: my_schema
contract_ref: config/entities.yaml

concepts:
  Account:
    entity: accounts                   # Points to entities.yaml
    fields:
      account_id: account_id           # CSV col → canonical field (direct)
      party_id: client_id              # CSV col → canonical field (renamed!)
      product_code: account_type       # CSV col → canonical field (renamed!)
```

**Steps:**
1. Create `config/bindings/my_schema.yaml`
2. List all concepts and their field mappings
3. Use this binding in your profile (next step)

**Document:** `docs/three_layers_schema_binding.md` (Layer 3)

---

### 2.4: Define Runtime Configuration
**File to edit:** `config/profiles/my_schema_local.yaml`

```yaml
config_version: 1
profile: my_schema_local
runtime:
  target: local
source:
  backend: offline_local_flat
  entity_map_ref: config/entities.yaml
  data_dir: data_generator/output        # Where your CSVs are
  binding: my_schema                     # Which binding to use
analytics:
  backend: duckdb_local
llm:
  provider: ollama
  model: qwen2.5:7b
state:
  backend: sqlite
  path: var/agent_traces.db
output:
  backend: local
  path: var/insights
cost:
  paid_llm_calls_allowed: false
  paid_cloud_services_allowed: false
```

**Steps:**
1. Copy `config/profiles/legacy_local.yaml` as template
2. Change profile name and paths to match your schema
3. Point to your data directory and binding

**Document:** `docs/data_flow_runtime.md` (Section: "Step 1: CSV Files")

---

### 2.5: Define Signal → Category Mappings
**File to edit:** `config/domains_<your_domain>.yaml`

```yaml
deposits:
  allowed_actions:
    - "RM to review the client's deposit activity"
  signals:
    cash_buildup:
      category: TREASURY_OPPORTUNITY
      why_now: "Balance has been climbing steadily..."
      hypothesis: "A sustained rise in deposit balance usually means idle cash..."

combinations:
  - name: growth_outrunning_working_capital
    when: [cash_buildup, facility_utilization_spike]
    category: FINANCING_NEED
    hypothesis: "Deposits building while facility drawn harder..."
```

**Steps:**
1. Review which detectors you want to use (all 11? subset?)
2. For each detector signal, define category + hypothesis
3. Add combination rules for multi-signal scenarios

**Document:** `docs/signal_to_category_logic.md` (Layers 1 & 2)

---

## STAGE 3: RUN THE SYSTEM

### 3.1: Validate Your Configuration

```bash
# Step 1: Validate schema against CSV
python -m datainsights.sources.csv_source \
  --csv-path data_generator/output \
  --validate-schema config/entities.yaml

# Step 2: Validate binding
python -c "from datainsights.semantic.binding import load_binding; print(load_binding('my_schema'))"

# Step 3: Validate domains config
python -c "from datainsights.domain_registry import DOMAINS; print(DOMAINS.keys())"
```

**Expected:** No errors. If errors, go back to Stage 2 and fix.

---

### 3.2: Run the Pipeline

```bash
# Full book evaluation
python -m agents.orchestrator \
  --profile my_schema_local

# Single client (for debugging)
python -m agents.orchestrator \
  --profile my_schema_local \
  --party-id PRTY0001
```

**Expected output:**
- Recommendations printed to terminal
- worklist CSV written to `var/insights/`
- SQLite trace DB updated in `var/agent_traces.db`

---

### 3.3: View Results

```bash
# View the worklist
open var/insights/worklist_*.csv

# Or start the dashboard (requires Flask)
python -m dashboard.app --profile my_schema_local
# Open browser to http://localhost:5000
```

---

## STAGE 4: OPTIONAL — Advanced Configuration

### 4.1: Adjust Detector Thresholds
**File:** `config/rules.yaml`

```yaml
large_incoming_payment:
  threshold_mad: 2.1        # Transactions > 2.1× rolling MAD
  window_days: 90

cash_buildup:
  trend_threshold: 1.2      # 20% increase = trending up
```

Change these if detectors are too sensitive or not sensitive enough.

**Document:** `docs/all_detectors_and_signals.md`

---

### 4.2: Add a New Detector
If you spot a pattern in your data:

**Steps:**
1. Create `detection_engine/your_detector.py` (copy an existing detector as template)
2. Write logic: read CSV → aggregate → produce Signal
3. Add to `config/domains_<domain>.yaml`: map signal_type → category
4. Run pipeline

**Document:** `docs/handling_domain_gaps.md` (Section 2)

---

### 4.3: Enable ML Baseline (Optional)
```bash
# Compare deterministic vs. IsolationForest baseline
python -m datainsights.ml.compare_baselines --profile my_schema_local

# If IsolationForest wins, enable in config/rules.yaml:
detectors:
  large_incoming_payment:
    baseline: isolation_forest
```

**Document:** `docs/ML_STATUS_AND_STRATEGY.md`

---

## The Five Configuration Files You'll Edit

| File | Purpose | When |
|------|---------|------|
| `config/entities.yaml` | Describe your CSV structure | First (Stage 2.1) |
| `config/semantic_model.yaml` | Define canonical field names | Second (Stage 2.2) |
| `config/bindings/your_schema.yaml` | Map physical → canonical | Third (Stage 2.3) |
| `config/profiles/your_schema_local.yaml` | Runtime config (paths, backend) | Fourth (Stage 2.4) |
| `config/domains_*.yaml` | Signal → category mapping | Fifth (Stage 2.5) |

**Sequence matters.** Each depends on the previous.

---

## Document Cross-Reference Map

```
User asks...                          → Read this doc
────────────────────────────────────────────────────
"How does data flow?"                 → data_flow_runtime.md
"What's this binding thing?"          → three_layers_schema_binding.md
"How are categories assigned?"        → signal_to_category_logic.md
"How do I add my own data?"          → This guide (Stage 2) + handling_domain_gaps.md
"What detectors exist?"               → all_detectors_and_signals.md
"Is there ML?"                        → ML_STATUS_AND_STRATEGY.md
"How do I understand a recommendation?" → README_FOR_DASHBOARD_VIEWERS.md
"Why does my detector not fire?"      → yaml_rules_executed_against_data.md
"I want to understand everything"     → Read in order: architecture → three_layers → data_flow → README → signal_to_category → yaml_rules → FDM → ML_status → all_detectors
```

---

## Checklist: Before You Run

- [ ] CSV files exist in `data_generator/output/` (or configure your own path)
- [ ] `config/entities.yaml` has all your tables with required columns
- [ ] `config/semantic_model.yaml` defines canonical field names
- [ ] `config/bindings/your_schema.yaml` maps physical → canonical
- [ ] `config/profiles/your_schema_local.yaml` points to your data
- [ ] `config/domains_*.yaml` maps signals → categories
- [ ] Validation passes: `python -m datainsights.sources.csv_source --validate-schema config/entities.yaml`
- [ ] Python dependencies installed: `pip install -r requirements.txt`
- [ ] Ollama running (if you want LLM narration): `ollama serve`

---

## Troubleshooting Quick Reference

| Problem | Solution |
|---------|----------|
| "Column X not found" | Add X to required_columns in entities.yaml, then re-validate |
| "Binding refers to non-existent entity" | Check entity name matches exactly in entities.yaml |
| "Signal fires but no recommendation" | Check domains_*.yaml maps that signal_type to a category |
| "Recommendation has wrong category" | Check combination rules in domains_*.yaml aren't overriding |
| "Detector produces no signals" | Check thresholds in config/rules.yaml, run single-client with debug output |
| "No CSV found" | Check data_dir path in profiles/your_schema_local.yaml |

---

## Next Steps After Stage 3

1. **See dashboards working** → Understand the visual feedback
2. **Collect RM outcomes** → Enable ground-truth labels (needed for ML, validation)
3. **Add new detectors** → Use `all_detectors_and_signals.md` to spot patterns
4. **Optimize thresholds** → Use domain knowledge to tune sensitivity
5. **Enable ML** → Once labels exist, run `compare_baselines.py`

---

## How These Docs Relate

```
                        USER GUIDE (you are here)
                                 ↓
                ┌────────────────┼────────────────┐
                ↓                ↓                ↓
            UNDERSTAND        CONFIGURE         RUN
                ↓                ↓                ↓
        (9 reference docs)  (5 YAML files)  (3 commands)
                
        Architecture.md      entities.yaml     validate
           ↓ (high-level)    semantic_model   ↓
        Three_layers        binding.yaml      run
           ↓ (schema)        profiles/*.yaml  ↓
        Data_flow           domains_*.yaml    view
           ↓ (runtime)                        
        README_dashboard                      
           ↓ (visualization)                  
        Signal_to_category                    
           ↓ (logic)                          
        Yaml_rules                            
           ↓ (execution)                      
        FDM_model                             
           ↓ (inspiration)                    
        ML_status                             
           ↓ (future)                         
        All_detectors                         
           (reference)                        
```

---

## Summary: What You're Building

```
Your data (CSV)
    ↓
config/entities.yaml (describe it)
    ↓
config/semantic_model.yaml (canonical names)
    ↓
config/bindings/your_schema.yaml (translate)
    ↓
config/profiles/your_schema_local.yaml (runtime config)
    ↓
config/domains_*.yaml (signal → category rules)
    ↓
Pipeline runs automatically
    ↓
Recommendations on dashboard
```

**Each step is a YAML file. One small file per step. Five files total.**

**No code to write. No Python changes needed.**

---

## Final Note: You Have Everything You Need

- ✅ Conceptual docs (9 reference docs in UNDERSTAND stage)
- ✅ Configuration guide (Stage 2, with file-by-file instructions)
- ✅ Execution steps (Stage 3, three simple commands)
- ✅ Troubleshooting (quick reference table)
- ✅ Cross-reference map (where to find what)

**Start with Stage 1 (read 9 docs in order). Then Stage 2 (edit 5 YAML files). Then Stage 3 (run 3 commands).**

Nothing else needed. No code reading required. This guide closes all the gaps.

