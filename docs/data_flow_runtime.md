# Data Flow at Runtime: From CSV → Canonical Model → Dashboard

This document traces **exactly how data flows** from the CSV files through adapters, bindings, detectors, and onto the dashboard you're viewing.

---

## The Dashboard You're Looking At

Your screenshot shows:
- **Left sidebar:** List of clients (PRTY0036, PRTY0016, PRTY0050, etc.)
- **Main panel:** "Prepare for the client call" — recommendation details
- **Bottom:** Evidence, confidence level, RM response options
- **Source:** This is the output of the `evaluate_book()` pipeline running against legacy CSV data

**Source of truth:** These recommendations are **computed fresh** every run from the CSVs, not cached elsewhere. Each row = one `Recommendation` object that flowed through the entire pipeline.

---

## The Complete Data Flow (Step by Step)

```
┌─────────────────────────────────────────────────────────────────────┐
│ Step 1: CSV Files on Disk                                           │
│ data_generator/output/                                              │
│  ├─ accounts.csv (account_id, client_id, account_type, ...)        │
│  ├─ transactions.csv (transaction_id, amount, booking_date, ...)   │
│  └─ balances.csv (account_id, date, closing_balance, ...)          │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 2: Adapter (OfflineLocalSource)                               │
│ datainsights/sources/offline_local.py                               │
│                                                                     │
│ - Loads CSVs into DuckDB (in-memory, no network)                   │
│ - Validates against config/entities.yaml schema                    │
│ - Refuses to read protected_evaluator_only/* files                 │
│ - Returns pandas DataFrames with PHYSICAL column names:             │
│   ├─ accounts['client_id'], accounts['account_type']               │
│   ├─ transactions['amount'], transactions['booking_date']          │
│   └─ balances['closing_balance']                                   │
│                                                                     │
│ Code: source.read_entity('accounts') → pd.DataFrame                │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 3: Binding (config/bindings/legacy.yaml)                      │
│ Loaded by: datainsights/semantic/binding.py                        │
│                                                                     │
│ Translation rules:                                                  │
│   accounts:                                                         │
│     entity: accounts                                                │
│     fields:                                                         │
│       party_id: client_id         ← CSV column client_id            │
│       product_code: account_type  ← CSV column account_type        │
│       account_id: account_id                                        │
│                                                                     │
│   transactions:                                                     │
│     entity: transactions                                            │
│     fields:                                                         │
│       amount: amount              ← CSV column amount              │
│       posted_at: booking_date     ← CSV column booking_date        │
│       account_id: account_id                                        │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 4: CanonicalSource (datainsights/semantic/canonical.py)       │
│                                                                     │
│ Applies binding at runtime:                                        │
│                                                                     │
│ def read_account(party_id='CLI-001'):                              │
│   df = source.read_entity('accounts')  # Physical frame             │
│   # df has columns: account_id, client_id, account_type, ...       │
│                                                                     │
│   # Binding maps:                                                   │
│   canonical_df = df.rename(columns={                               │
│       'client_id': 'party_id',      # CSV col → canonical field    │
│       'account_type': 'product_code'                               │
│   })                                                                │
│                                                                     │
│   # Return ONLY canonical field names:                             │
│   # canonical_df has: account_id, party_id, product_code, ...     │
│   return canonical_df[canonical_df['party_id'] == 'CLI-001']       │
│                                                                     │
│ Result: Detector receives canonical names only                     │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 5: Detector (detection_engine/*.py)                           │
│                                                                     │
│ Example: large_incoming_payment detector                           │
│                                                                     │
│ def detect(transactions: DataFrame) -> List[Signal]:               │
│   # Detector NEVER sees CSV column names                           │
│   # It uses canonical names from binding:                         │
│                                                                     │
│   large_txns = transactions[                                       │
│       transactions['amount'] > baseline_threshold  # Canonical!    │
│   ]                                                                │
│                                                                     │
│   return [                                                          │
│       Signal(                                                       │
│           party_id=row['party_id'],  # From binding                │
│           signal_type='large_incoming_payment',                    │
│           amount=row['amount'],      # From binding                │
│           confidence=0.95                                          │
│       )                                                             │
│   ]                                                                 │
│                                                                     │
│ Input: Canonical data (from Step 4)                                │
│ Output: List of Signal objects                                     │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 6: Signal Bus & Hypothesis Assembler                          │
│ datainsights/correlation/signal_bus.py                             │
│                                                                     │
│ Receives signals from ALL detectors (Deposits, Lending, Risk):     │
│   Signal(party_id='CLI-001', type='large_incoming_payment')        │
│   Signal(party_id='CLI-001', type='cash_buildup')                 │
│   Signal(party_id='CLI-001', type='facility_utilization_spike')    │
│                                                                     │
│ Hypothesis Assembler applies combination rules:                    │
│   If (large_payment AND cash_buildup):                             │
│       → Category = FINANCING_NEED (client needs working capital)   │
│   Strength = ★★★★ (multiple signals confirm)                       │
│                                                                     │
│ Output: Hypothesis object per (client, category)                   │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 7: Domain Agent Narration (agents/domain_agent.py)            │
│                                                                     │
│ INPUT: Hypothesis + evidence (deterministic facts)                 │
│   - Party: PRTY0036 (SME, Manufacturing, ES)                       │
│   - Signal: Incoming payment 500K EUR (2.1x baseline)              │
│   - Evidence: Last 30 days recurring pattern +50%                  │
│   - Exogenous: Tender award TED notice 2 days ago                  │
│                                                                     │
│ LLM (local Ollama) narrates:                                        │
│   "Winning a public tender creates a cash-flow gap..."             │
│                                                                     │
│ VALIDATION layer (not skipped):                                    │
│   - ✅ Currency consistent (EUR)?                                  │
│   - ✅ No banned terms (e.g., "I predict", "likely", "could")?     │
│   - ✅ Numeric traceability (500K → evidence)?                     │
│   - ❌ If any fail → fallback to template (deterministic)           │
│                                                                     │
│ OUTPUT: NarratedSignal (text + verified metadata)                  │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 8: Recommendation Object                                      │
│ datainsights/model.py                                              │
│                                                                     │
│ Recommendation {                                                    │
│   recommendation_id: 'REC-PRTY0036-20260920-1',                    │
│   party_id: 'PRTY0036',                                            │
│   nba_category: 'FINANCING_NEED',                                  │
│   hypothesis: 'Incoming payment pattern shift confirms tender...',│
│   sized_action: 'Offer €800k working capital (illustrative)',       │
│   evidence: [EVENT_FINANCIAL.456789, AGREEMENT.123],               │
│   confidence: 0.95,                                                │
│   signals_supporting: ['large_incoming_payment', 'revenue_growth'],│
│   exogenous_amplifier: 'TED_API.tender_award',                     │
│   as_of_date: 2026-09-20,                                          │
│   investigation_notes: 'Investigator A2 proposed rate lock...',     │
│ }                                                                   │
│                                                                     │
│ This object is now ready for:                                      │
│   1. Ranking (highest confidence + revenue per category)           │
│   2. Dedup (keep strongest per (party, category))                  │
│   3. Serialization (to worklist CSV, Pega mock, MIMO JSON)         │
└─────────────────────────────────────────────────────────────────────┘
                               ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 9: Sinks (datainsights/sinks/)                                │
│                                                                     │
│ Same Recommendation object is rendered by multiple sinks:          │
│                                                                     │
│ A) RM Worklist (CSV + Dashboard)                                   │
│    └─ fdm_worklist.py renders your screenshot                     │
│       - Left table: client list + signal metadata                 │
│       - Right panel: "Prepare for client call" narrative            │
│                                                                     │
│ B) MIMO JSON (mock CRM format)                                     │
│    └─ mimo_placeholder.py                                          │
│                                                                     │
│ C) Pega Event (NOT RUN, marked as contract-only)                   │
│    └─ pega_event_mock.py                                           │
│                                                                     │
│ All sinks receive SAME Recommendation — never compute own          │
│ category/hypothesis/sizing (that's centralized in Step 8).         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Where Events Are Identified & Tracked

### Endogenous Events (from CSV data)

**Detector-specific tracking:**

1. **Deposits detector** monitors each account:
   - Watches `transactions['amount']` → flags if > 2.1× rolling 90-day MAD
   - Watches `balances['closing_balance']` → flags if trending up 30+ days
   - Watches transaction frequency → flags dormancy if 90+ days silent

2. **Lending detector** monitors each facility:
   - Watches `AGREEMENT_DAILY_BALANCE / AGREEMENT_ORIG_LIM` → flags if > 85%
   - Watches `AGREEMENT.close_date` → flags if < 90 days away
   
3. **Risk detector** monitors each party:
   - Watches `PARTY.RSK_GRD_CD` → flags if migrated down
   - Would watch `COLLATERAL_ITEM_VALUE` (not in legacy CSV yet)

**Trigger:** Detector runs on-demand or on schedule (via `datainsights/monitor.py`). Every client's concept data is assembled fresh from CSV at that moment.

### Exogenous Events (external news, optional)

**Not in your current CSV — loaded separately:**

If configured:
- `external_events/ingest_notices.ingest()` reads news from `external_events/output/external_events.csv`
- Agent A1 (`event_extraction_agent.py`) validates: "Does this event actually apply to this client?"
- Example: TED tender award → matches client NACE code → enqueues that client for re-evaluation

**In your screenshot:** The exogenous amplifier is listed at the bottom: `EVENT_FINANCIAL.A6R988868;jFF=2235:88-21`.

---

## Is the Dashboard the Only Source of Truth?

**No — but sort of?**

### What's Authoritative

The **current worklist** (dashboard) is the **latest run's output**. It's derived from:
1. **Data:** CSVs on disk (or Snowflake in production)
2. **Logic:** Detectors in `detection_engine/`
3. **Rules:** `config/rules.yaml` thresholds

If you change a detector or a CSV and re-run, the worklist updates. No other system confirms it — it's your single source of truth for "what should the RM work on right now."

### What's NOT Authoritative (Evaluator Only)

- `data_generator/output/protected_evaluator_only/trigger_events.csv` — ground truth labels
  - Used **only** by `evaluation/evaluate.py` to score detectors
  - Never fed back to detector logic (hard boundary)
  - Not visible in the dashboard, by design

---

## "Explore a Source" in the Sidebar

**What it does:**
- Not a different source of truth — it's a **diagnostic tool**
- Lets you browse the raw concept data (after binding is applied)
- Example: Click "Explore a source" → select Account → filter by party_id 'PRTY0036' → see raw canonical data that detector used

**What it's NOT:**
- Not a second opinion on whether a recommendation is correct
- Not an override mechanism
- Just transparency into what data the detector saw

---

## Event Lifecycle Per Customer

Example: Client PRTY0036 (your screenshot)

```
T-0: Synthetic data generated
     ├─ transactions.csv has 3 entries for this client
     ├─ accounts.csv has 1 entry
     └─ balances.csv has 30 days of daily entries

T+1h: Run triggered (scheduled or manual)
      ├─ OfflineLocalSource loads all CSVs
      ├─ CanonicalSource applies legacy binding
      ├─ Detector reads canonical Account + Transaction data
      └─ Finds: amount 500K, baseline 237K → signal = large_incoming_payment

T+1h+30s: Signal Bus correlates
          ├─ Receives 3 signals: large_payment + cash_buildup + revenue_growth
          └─ Hypothesis Assembler: "All point to FINANCING_NEED" → confidence 0.95

T+1h+40s: Domain Agent narrates
          ├─ Ollama writes: "Winning a public tender..."
          ├─ Validation layer: currency ✓, traceability ✓
          └─ Output: NarratedSignal

T+1h+50s: Recommendation created + ranked
          ├─ Sorted by confidence and revenue potential
          └─ Sent to sinks

T+1h+51s: Dashboard updated
          ├─ fdm_worklist.py renders your screenshot
          └─ RM sees PRTY0036 in list with narrative

T+Now: You click on PRTY0036
       └─ Dashboard shows full recommendation details
```

---

## If You Change Something

### Change CSV
```bash
# Edit data_generator/output/accounts.csv (change a balance)
# Re-run pipeline
python -m agents.orchestrator --profile legacy_local
# Dashboard updates immediately (no cache)
```

### Change Detector Threshold
```yaml
# config/rules.yaml
large_incoming_payment:
  threshold_mad: 2.1  # Change to 2.5
# Re-run
python -m agents.orchestrator --profile legacy_local
# Different clients may now be flagged/unflagged
```

### Change Binding (if adding new column)
```yaml
# config/bindings/legacy.yaml
concepts:
  Account:
    fields:
      new_field: csv_column  # Add
# Validate: python -m datainsights.sources.csv_source --validate-schema config/entities.yaml
# Re-run
# Detector can now use canonical new_field
```

---

## Summary: The Flow

```
CSV → Adapter (validates schema) 
   → Binding (renames columns)
   → CanonicalSource (returns canonical names only)
   → Detector (uses canonical names)
   → Signal Bus (correlates)
   → Hypothesis Assembler (combines)
   → Domain Agent (narrates)
   → Recommendation (scored)
   → Sink (rendered on dashboard)
```

**Each step is stateless** — no intermediate storage. Every run re-computes from CSV.

**Adapters are the filter** — they validate and refuse bad data upfront.

**Binding is the translator** — it lets the same detector work against different physical schemas.

**Dashboard is the endpoint** — what you see is exactly what flowed through the pipeline.

