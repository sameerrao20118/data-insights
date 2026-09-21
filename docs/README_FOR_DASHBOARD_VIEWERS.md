# For Dashboard Viewers: How Your Worklist Gets Built

**You're looking at a live output of the DataInsights pipeline.** This guide explains where that data comes from and how it flows.

---

## Quick Answer to Your Questions

**Q: How do adapters come into the picture while reading schema-based information?**

A: 
1. CSV files exist on disk (data_generator/output/)
2. Adapter (OfflineLocalSource) loads CSV → validates against `config/entities.yaml` schema
3. If CSV columns don't match schema, adapter rejects it with actionable error
4. If valid, adapter returns pandas DataFrame with physical column names
5. Binding applies (Step 3 below), renaming to canonical names
6. Detector receives canonical names only

**Q: How is data actually pulled in for analysis based on what events are identified?**

A:
- Detector scans the canonical data (from binding) looking for patterns
- Example: "Is this transaction amount > 2.1× the 90-day rolling median?"
- If yes, detector creates a Signal
- All signals per client are correlated by Signal Bus
- Correlation rules in `config/domains_*.yaml` combine signals into one Recommendation
- Recommendation is rendered on the dashboard

**Q: Is the dashboard the only source of truth?**

A:
- Yes, the **worklist** (what you see) is the source of truth for "what should RM work on"
- It's computed fresh every run from CSVs
- No other system needs to confirm it
- But see "Explore a source" in the sidebar if you want to debug (see the raw data the detector used)

**Q: Is "Explore a source" the only truth?**

A:
- No, it's a diagnostic tool, not an authority
- It shows raw canonical data that detector consumed
- Use it to verify "Did the detector actually see this data?"

---

## The 9-Step Pipeline (Summary)

See `docs/data_flow_runtime.md` for detailed code walkthrough.

| Step | Component | Input | Output |
|------|-----------|-------|--------|
| 1 | CSV files | Nothing (static data on disk) | Raw bytes |
| 2 | Adapter (OfflineLocalSource) | CSV path + schema contract | Validated DataFrame with physical column names |
| 3 | Binding | Physical DataFrame | Renamed DataFrame (canonical column names) |
| 4 | CanonicalSource | Physical DataFrame + Binding | Canonical DataFrame (detector-ready) |
| 5 | Detector | Canonical DataFrame | List of Signal objects |
| 6 | Signal Bus | Signals from all domains | Grouped signals per client |
| 7 | Hypothesis Assembler | Grouped signals + combination rules | Hypothesis per (client, category) |
| 8 | Domain Agent | Hypothesis + evidence | Narrated text (validated) |
| 9 | Recommendation Builder | Narrated signal + metadata | Recommendation object |
| 10 | Sink (fdm_worklist.py) | Recommendation | Your dashboard |

---

## File Locations

| What | Where | Purpose |
|-----|-------|---------|
| Raw CSV data | `data_generator/output/` | Source of all facts |
| Schema contract | `config/entities.yaml` | Defines which columns must exist |
| Binding rules | `config/bindings/legacy.yaml` | Translates CSV names → canonical names |
| Canonical vocab | `config/semantic_model.yaml` | Defines canonical field names |
| Detector code | `detection_engine/` | Looks for patterns |
| Combination rules | `config/domains_deposits.yaml`, `..._lending.yaml` | How signals combine into hypothesis |
| Detectors' thresholds | `config/rules.yaml` | "Large payment" = amount > 2.1× MAD |
| Ground truth labels | `data_generator/output/protected_evaluator_only/` | Never read by detector, only by evaluator |

---

## Example: How PRTY0036 Got On Your Worklist

```
1. CSV Load
   accounts.csv row: account_id='AC-001', client_id='PRTY0036', account_type='current'
   transactions.csv rows (3):
     - transaction_id='T-1', account_id='AC-001', amount=500000, booking_date='2026-09-20'
     - transaction_id='T-2', account_id='AC-001', amount=240000, booking_date='2026-08-15'
     - ... (more history for baseline)
   
   Adapter validates: ✓ All columns present, types correct

2. Binding Applied
   CSV column 'client_id' → canonical field 'party_id'
   CSV column 'account_type' → canonical field 'product_code'
   
3. Detector (large_incoming_payment)
   Reads: 
     parties['party_id'] = 'PRTY0036'
     transactions = [amount=500k, 240k, ...]
     baseline_mad = median(240k, ...) = 237k
   
   Check: 500k > 2.1 * 237k? YES (500k > 497k)
   Signal: large_incoming_payment (confidence 0.95)

4. Signal Bus Sees
   Signal 1: large_incoming_payment (PRTY0036)
   Signal 2: cash_buildup (PRTY0036) — balances trending up
   Signal 3: revenue_pattern_change (PRTY0036) — transaction frequency +50%
   
5. Hypothesis Assembler
   Rule: "If (large_payment AND revenue_growth): category = FINANCING_NEED"
   Result: Recommendation(party_id='PRTY0036', category=FINANCING_NEED, strength=★★★★)

6. Domain Agent
   Input: "Client PRTY0036 (SME, Manufacturing, ES) has 500K incoming payment"
   Ollama writes: "Winning a public tender creates a cash-flow gap..."
   Validation: ✓ Currency consistent, ✓ Traceability to evidence
   
7. Recommendation Scored
   confidence: 0.95
   revenue_potential: €21,200 (interest on €800k working capital)
   
8. Rendered on Dashboard
   Your screenshot shows this recommendation at top of worklist
```

---

## When Something Changes

### If you change the CSV
```bash
# Edit data_generator/output/accounts.csv
# Re-run the pipeline
python -m agents.orchestrator --profile legacy_local
# Dashboard updates immediately (stateless — no cache)
```

### If you change a detector threshold
```yaml
# Edit config/rules.yaml
large_incoming_payment:
  threshold_mad: 2.5  # was 2.1
# Re-run
# Different clients flagged/unflagged
```

### If you add a new column
See `docs/handling_domain_gaps.md` for the 3-step process.

---

## What's NOT On the Dashboard (By Design)

1. **Ground truth labels** (`protected_evaluator_only/trigger_events.csv`)
   - Only evaluator reads this
   - Used to score detectors offline, not to influence recommendations
   
2. **FinCrime signals** (AML alerts, PEP lists)
   - Separate governance path
   - Would contaminate sales recommendations if mixed
   
3. **Intermediate signals that got filtered**
   - Example: "Rating downgrade detected but client has no facility"
   - Signals that don't reach Recommendation threshold are not shown

---

## For Debugging: "Explore a Source"

If you want to verify the detector saw the right data:

1. Click "Explore a source" in sidebar
2. Select "Account" concept
3. Filter by party_id
4. You'll see the canonical data frame (after binding applied)
5. This is exactly what detector read

**It's not the source of truth** — just transparency. The dashboard worklist is the source of truth.

---

## References

- **Full data flow diagram:** `docs/data_flow_runtime.md`
- **Schema & binding mechanics:** `docs/three_layers_schema_binding.md`
- **FDM alignment:** `docs/fdm_natwest_model.md`
- **Adding new columns:** `docs/handling_domain_gaps.md`
- **Architecture:** `docs/architecture.md`

