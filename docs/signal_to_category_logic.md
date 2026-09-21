# Signal → Category: How Clients Get Shortlisted

**Question:** "Based on what signals are identified, where is the logic for shortlisting a client against one of the categories?"

**Answer:** All in config files. Two layers of logic:

1. **Per-signal mapping** (`config/domains_fdm.yaml`) — Each detector signal gets a default category
2. **Combination rules** (`config/domains_fdm.yaml`) — Multiple signals together can change the category

No code hardcoding. No if-statements in Python. Just YAML configuration that the `HypothesisAssembler` reads.

---

## Layer 1: Per-Signal Mapping (Default Category)

File: `config/domains_fdm.yaml`, section `deposits:` → `signals:`

### Example: Deposits Domain

```yaml
deposits:
  signals:
    cash_buildup:                    # ← Detector signal name
      category: TREASURY_OPPORTUNITY # ← Default category
      why_now: "Balance has been climbing steadily..."
      hypothesis: "A sustained rise in deposit balance..."
    
    large_incoming_payment:
      category: TREASURY_OPPORTUNITY # ← Same default
      why_now: "A payment landed well above this client's pattern..."
      hypothesis: "A payment landing well above..."
    
    dormancy:
      category: ADVISORY_ONLY        # ← Different default
      why_now: "Account has gone quiet..."
      hypothesis: "An account with no recent activity..."
    
    revenue_pattern_change:
      category: FINANCING_NEED       # ← Different again
      why_now: "Incoming pattern has structurally shifted..."
      hypothesis: "A structural step-change usually reflects growth..."
```

### How This Works at Runtime

When the Signal Bus receives signals:

```python
# Detector found these signals for PRTY0036:
signals = [
    Signal(party_id='PRTY0036', signal_type='large_incoming_payment', confidence=0.95),
    Signal(party_id='PRTY0036', signal_type='cash_buildup', confidence=0.85),
]

# HypothesisAssembler looks up each in domains_fdm.yaml:
for signal in signals:
    domain_entry = load_domains_fdm()[signal.domain]  # 'deposits'
    signal_entry = domain_entry['signals'][signal.signal_type]  # 'large_incoming_payment'
    
    # Get default category from YAML
    default_category = signal_entry['category']  # 'TREASURY_OPPORTUNITY'
    hypothesis = signal_entry['hypothesis']     # Full text from YAML
```

---

## Layer 2: Combination Rules (Override Default)

File: `config/domains_fdm.yaml`, section `combinations:`

**The Problem:** 

Single signals are context-free:
- "Balance rising" → TREASURY_OPPORTUNITY (idle cash)
- "Facility being drawn" → FINANCING_NEED (need headroom)

But together:
- "Balance rising AND facility being drawn" → **Growth outrunning working capital**, not idle cash

**The Solution:** Explicit combination rules

### Example: Growth Outrunning Working Capital

```yaml
combinations:
  - name: growth_outrunning_working_capital
    when: [cash_buildup, facility_utilization_spike]  # BOTH must be present
    category: FINANCING_NEED                         # Override both defaults
    size_from: facility_utilization_spike             # Use facility spike's amount for sizing
    hypothesis: >-
      Deposits are building at the same time the credit facility is being 
      drawn harder -- the business is growing faster than its working capital, 
      and the cash on hand is committed, not idle. The conversation is facility 
      headroom, not placing surplus.
```

### How This Works at Runtime

```python
# Detector found these signals for PRTY0036:
signals = [
    Signal(party_id='PRTY0036', signal_type='cash_buildup', confidence=0.85),
    Signal(party_id='PRTY0036', signal_type='facility_utilization_spike', confidence=0.92),
]

# HypothesisAssembler first tries combinations (most specific rule wins)
combinations = load_domains_fdm()['combinations']

for rule in combinations:
    # Check: are ALL signals in rule['when'] present for this client?
    signals_present = {s.signal_type for s in signals}
    required_signals = set(rule['when'])
    
    if required_signals.issubset(signals_present):
        # MATCH! Use the rule's category, not individual defaults
        category = rule['category']        # FINANCING_NEED (overrides TREASURY_OPPORTUNITY)
        hypothesis = rule['hypothesis']    # Growth story, not idle cash story
        break
else:
    # No combination rule matched, fall back to strongest individual signal
    strongest_signal = max(signals, key=lambda s: s.confidence)
    category = get_signal_category(strongest_signal)
```

---

## The Six Categories (Where Clients Get Shortlisted)

File: `config/categories.yaml`

| Category | When Assigned | Revenue? | Example |
|---|---|---|---|
| **FINANCING_NEED** | Facility drawn hard, maturity soon, revenue growing, working capital gap | ✅ Yes | "Winning a tender creates cash-flow gap" |
| **TREASURY_OPPORTUNITY** | Balance building, idle cash, rate moving, fixed rate expiring | ✅ Yes | "Your cleared balances have been climbing steadily" |
| **HEDGING_NEED** | Multi-currency exposure, rate volatility | ✅ Yes | "You're carrying exposure across more than one currency" |
| **CAPEX_FINANCING** | Asset value rising, investment phase | ✅ Yes | "You may be investing ahead of a change" |
| **RISK_REVIEW** | Rating downgraded, collateral dropping, sanctions | ❌ No | "Internal review item — do not lead with this" |
| **ADVISORY_ONLY** | Dormancy, general relationship checkup | ❌ No | "No specific product yet, relationship check-in" |

---

## Real Example: PRTY0036 From Your Screenshot

### Step 1: Detectors Find Signals

```
Deposits detector scans transactions:
  - Transaction: €500,000 on 2026-09-20
  - 90-day rolling median: €237,000
  - Check: 500K > 2.1 × 237K?  YES (500K > 497K)
  → Signal: large_incoming_payment (confidence 0.95)

Deposits detector scans balances:
  - Balance trend last 30 days: +€50K per day (trending up)
  → Signal: cash_buildup (confidence 0.85)

Lending detector scans facilities:
  - Facility utilization: 45% (not > 85%)
  → No signal

Risk detector scans party:
  - Risk rating: unchanged
  → No signal
```

### Step 2: HypothesisAssembler Looks Up Defaults

```yaml
signals_found = ['large_incoming_payment', 'cash_buildup']

# Lookup each in domains_fdm.yaml:
large_incoming_payment:
  default_category: TREASURY_OPPORTUNITY
  why_now: "Payment landed above client's pattern..."

cash_buildup:
  default_category: TREASURY_OPPORTUNITY
  why_now: "Balance climbing steadily..."
```

Both point to TREASURY_OPPORTUNITY.

### Step 3: Check for Combination Rules

```yaml
combinations:
  - name: growth_outrunning_working_capital
    when: [cash_buildup, facility_utilization_spike]
    category: FINANCING_NEED
    # facility_utilization_spike NOT found, rule doesn't match
  
  - name: inflows_changing_while_dormant_elsewhere
    when: [revenue_pattern_change, dormancy]
    category: ADVISORY_ONLY
    # Neither signal found, rule doesn't match

# No rules matched, stick with strongest signal's default
strongest = large_incoming_payment (0.95 confidence)
final_category = TREASURY_OPPORTUNITY
```

### Step 4: Check Risk Suppression

```yaml
# Rule 1 in assemble(): if rating_downgrade or high_risk_flag, suppress revenue categories
if party.high_risk_flag or signals.rating_downgrade:
    final_category = RISK_REVIEW  # Blocks TREASURY_OPPORTUNITY

# PRTY0036 has no risk flags → TREASURY_OPPORTUNITY stands
```

### Step 5: Domain Agent Narrates

```
Input: 
  Category: TREASURY_OPPORTUNITY
  Signals: large_incoming_payment (0.95), cash_buildup (0.85)
  Party: PRTY0036 (SME, Manufacturing, ES)
  Evidence: €500K transaction, €50K/day balance trend

Ollama narrates (local LLM):
  "Winning a public tender creates a cash-flow gap between delivery 
   and payment. Your incoming payment pattern has shifted recently. 
   If you're taking on new work, we can look at working-capital lines 
   sized to the contract rather than to last year's balance sheet — 
   would that be useful to talk through?"

Validation layer checks:
  ✓ Currency consistent (EUR)
  ✓ No banned terms ("likely", "could", "predict")
  ✓ Numeric traceability (€500K → evidence)
  → Passes, narrative is accepted
```

### Final Output (Your Dashboard)

```
Recommendation(
  party_id='PRTY0036',
  nba_category='FINANCING_NEED',  # ← Wait, not TREASURY_OPPORTUNITY?
  hypothesis='Winning a public tender creates cash-flow gap...',
  signals=['large_incoming_payment', 'revenue_pattern_change'],
  confidence=0.95,
)
```

**Why FINANCING_NEED instead of TREASURY_OPPORTUNITY?**

If the detector also found `revenue_pattern_change` signal:
```yaml
revenue_pattern_change:
  category: FINANCING_NEED
  hypothesis: "A structural step-change...reflects genuine business growth"
```

Then the combination rule **might** apply:
```yaml
- name: inflows_shifting_facility_maturing  # or another rule
  when: [revenue_pattern_change, ...]
  category: FINANCING_NEED
```

Or simply: the strongest signal wins if no combination rule matched.

---

## The Decision Tree (Step by Step)

```
For each client at each run:

1. Detectors scan data
   ├─ deposits.py finds: cash_buildup, large_incoming_payment
   ├─ lending.py finds: facility_maturity_approaching
   ├─ risk.py finds: (nothing)
   └─ Results: 3 signals per client

2. Look up each signal in domains_fdm.yaml
   ├─ cash_buildup → TREASURY_OPPORTUNITY (default)
   ├─ large_incoming_payment → TREASURY_OPPORTUNITY (default)
   └─ facility_maturity_approaching → FINANCING_NEED (default)

3. Check combination rules (most specific first)
   ├─ Does rule "growth_outrunning_working_capital" match?
   │  when: [cash_buildup, facility_utilization_spike]
   │  NO (facility_utilization_spike not found)
   │
   ├─ Does rule "cash_rich_facility_maturing" match?
   │  when: [cash_buildup, facility_maturity_approaching]
   │  YES! → Use this rule's category
   │  category: FINANCING_NEED  ← Overrides the defaults
   │
   └─ Result: FINANCING_NEED (not TREASURY_OPPORTUNITY)

4. Apply risk suppression (Rule 1)
   ├─ Does this client have rating_downgrade? NO
   ├─ Does this client have high_risk_flag? NO
   └─ → FINANCING_NEED stands

5. Assemble Recommendation object
   ├─ category: FINANCING_NEED
   ├─ hypothesis: (from rule, narrated by LLM)
   ├─ confidence: min(0.95, 0.85) = 0.85
   └─ signals: [cash_buildup, facility_maturity_approaching]

6. Render on dashboard
   └─ Client appears under FINANCING_NEED tab
```

---

## Where This Config Lives

| File | Purpose |
|---|---|
| `config/categories.yaml` | Define all 6 NBA categories (labels, colors, revenue models) |
| `config/domains_fdm.yaml` | Map each detector signal to default category + combination rules |
| `datainsights/domain_registry.py` | Load config/domains_fdm.yaml at startup |
| `datainsights/correlation/hypothesis.py` | HypothesisAssembler reads domain config, applies rules |

---

## How to Change the Logic (No Code)

### Add a New Combination Rule

Edit `config/domains_fdm.yaml`:
```yaml
combinations:
  - name: my_new_rule
    when: [signal1, signal2, signal3]  # ALL must be present
    category: RISK_REVIEW
    hypothesis: "My explanation text"
```

Restart pipeline → rule is active, no code change needed.

### Change a Default Category

Edit `config/domains_fdm.yaml`:
```yaml
deposits:
  signals:
    dormancy:
      category: FINANCING_NEED  # was ADVISORY_ONLY
```

Restart → dormancy clients now show as FINANCING_NEED instead.

### Add a New Category

Edit `config/categories.yaml`:
```yaml
categories:
  MY_NEW_CATEGORY:
    label: My Label
    description: My description
    colour: '#ABC123'
    revenue_model: financing
```

Then map it in domains_fdm.yaml:
```yaml
combinations:
  - when: [signal_x]
    category: MY_NEW_CATEGORY
```

---

## Summary

**Shortlisting logic is 100% in YAML:**

1. **Default mapping:** Each signal → category (from `config/domains_fdm.yaml`)
2. **Combination rules:** Multiple signals → overridden category (from `config/domains_fdm.yaml`)
3. **Risk suppression:** Rating downgrade/high-risk flag → force RISK_REVIEW (hardcoded, can't be configured)
4. **Tiebreaker:** If multiple signals, strongest wins (if no rule matches)

**No Python hardcoding of business logic.** Change signal→category mappings by editing YAML and restarting.

