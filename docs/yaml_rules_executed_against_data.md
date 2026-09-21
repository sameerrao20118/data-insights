# How YAML Rules Execute Against Real Data

You're absolutely right: **YAML is just instructions**. The actual **logic execution** happens in Python code. Let me show you exactly where and how.

---

## The Three-Layer Stack

```
Layer 1: YAML Config (Instructions)
  ├─ config/categories.yaml ("Define these 6 categories")
  ├─ config/domains_fdm.yaml ("Map these signals → categories")
  └─ config/rules.yaml ("Use these thresholds")
         ↓ loaded at startup ↓
Layer 2: Python Registry (Memory)
  ├─ datainsights/category_registry.py (loads categories.yaml)
  ├─ datainsights/domain_registry.py (loads domains_fdm.yaml)
  └─ datainsights/ml/policy.py (loads rules.yaml)
         ↓ called at runtime ↓
Layer 3: Detector + Assembler (Execution)
  ├─ detection_engine/*.py (generates Signals from CSV data)
  ├─ datainsights/correlation/hypothesis.py (applies rules to Signals)
  └─ datainsights/sinks/*.py (renders recommendations on dashboard)
```

---

## Step 1: YAML is Loaded into Memory

### At Startup

```python
# datainsights/domain_registry.py
import yaml

with open('config/domains_fdm.yaml') as f:
    DOMAINS_CONFIG = yaml.safe_load(f)  # ← YAML becomes a Python dict

# DOMAINS_CONFIG now looks like:
# {
#   'deposits': {
#     'signals': {
#       'cash_buildup': {
#         'category': 'TREASURY_OPPORTUNITY',
#         'hypothesis': '...',
#         ...
#       }
#     }
#   },
#   'combinations': [
#     {
#       'name': 'growth_outrunning_working_capital',
#       'when': ['cash_buildup', 'facility_utilization_spike'],
#       'category': 'FINANCING_NEED',
#       ...
#     }
#   ]
# }
```

**This happens once at startup** — config is read into memory as a Python dictionary.

---

## Step 2: Detector Runs Against Real CSV Data

### CSV File

```csv
# data_generator/output/accounts.csv
account_id,client_id,account_type,currency,credit_limit,open_date
AC-001,CLI-001,current,EUR,50000,2024-01-01
AC-002,CLI-001,savings,EUR,0,2024-02-15
```

### Detector Code Reads CSV

```python
# detection_engine/deposits_detector.py

def detect_cash_buildup(accounts: pd.DataFrame, balances: pd.DataFrame) -> list[Signal]:
    """
    Real logic: Check if closing_balance is trending up.
    THIS IS WHERE YAML RULES MET DATA.
    """
    signals = []
    
    for account_id in accounts['account_id']:
        # Get this account's balance history
        account_balances = balances[balances['account_id'] == account_id].sort_values('date')
        
        if len(account_balances) < 30:
            continue  # Need 30+ days of history
        
        # Last 30 days
        recent = account_balances.tail(30)['closing_balance'].values
        
        # Check: is balance trending up?
        # Python logic: compare first 10 days to last 10 days
        early_avg = recent[:10].mean()
        recent_avg = recent[-10:].mean()
        
        if recent_avg > early_avg * 1.2:  # 20% increase = trending up
            # MATCH! Create a Signal
            signals.append(Signal(
                party_id=accounts[accounts['account_id'] == account_id]['client_id'].iloc[0],
                signal_type='cash_buildup',  # ← This string links back to YAML
                magnitude=0.85,
                domain='deposits',
                raw_measure={
                    'current_balance': float(recent[-1]),
                    'prior_balance': float(recent[0]),
                    'currency': 'EUR'
                }
            ))
    
    return signals
```

**Key:** The detector produces a `Signal` object with `signal_type='cash_buildup'`. That string is the **bridge** between CSV data and YAML config.

---

## Step 3: YAML Rules Lookup (The Assembler)

### When Assembler Receives Signals

```python
# datainsights/correlation/hypothesis.py - assemble() function

def assemble(prty_id: str, signals: list[Signal], **kwargs) -> Recommendation:
    """
    This is where YAML rules are actually APPLIED to real signals from real data.
    """
    
    # Input: signals from detector (based on real CSV data)
    # signals = [
    #   Signal(signal_type='cash_buildup', magnitude=0.85, ...),
    #   Signal(signal_type='facility_utilization_spike', magnitude=0.92, ...),
    # ]
    
    # STEP 1: Look up the strongest signal's default category
    strongest = max(signals, key=lambda s: s.magnitude)  # facility_utilization_spike
    
    # Look it up in YAML (now in memory as DOMAINS_CONFIG):
    category = _category_for(strongest.signal_type)
    # _category_for() does:
    #   domain = get_domain_for_signal(signal_type)  # 'lending'
    #   return DOMAINS_CONFIG[domain]['signals'][signal_type]['category']
    #   # DOMAINS_CONFIG['lending']['signals']['facility_utilization_spike']['category']
    #   # → returns 'FINANCING_NEED'
    
    # STEP 2: Check combination rules
    combination = _matching_combination({s.signal_type for s in signals})
    # _matching_combination() does:
    #   signal_types = {'cash_buildup', 'facility_utilization_spike'}
    #   for rule in DOMAINS_CONFIG['combinations']:  # Loop through YAML rules
    #       if set(rule['when']).issubset(signal_types):  # Both signals present?
    #           return rule  # Found a match!
    #
    # Check rule: growth_outrunning_working_capital
    # rule['when'] = ['cash_buildup', 'facility_utilization_spike']
    # signal_types = {'cash_buildup', 'facility_utilization_spike'}
    # Subset? YES! ✓
    
    if combination is not None:
        # Override the default category with the rule's category
        category = combination['category']  # 'FINANCING_NEED' (from YAML)
        hypothesis = combination['hypothesis']  # Full text from YAML
        # combination_rule = 'growth_outrunning_working_capital'
    
    # STEP 3: Apply risk suppression (Rule 1)
    if high_risk_flag and is_revenue(category):  # is_revenue() reads categories.yaml
        category = 'ADVISORY_ONLY'  # Override to defensive category
    
    # STEP 4: Size the offer
    if is_revenue(category):
        sized_offer = _size_endogenous(
            sizing_signal,
            sizing_rules=RULES_CONFIG['fdm_endogenous_sizing']  # From rules.yaml
        )
        # _size_endogenous() reads YAML-configured sizing parameters
```

---

## Real Example: PRTY0036 from Your Dashboard

### Step 1: CSV Data Exists

```csv
# data_generator/output/balances.csv
account_id,date,opening_balance,closing_balance
AC-001,2026-09-01,100000,150000
AC-001,2026-09-02,150000,175000
...
AC-001,2026-09-20,490000,500000  ← Balance trending up
```

### Step 2: Detector Scans CSV

```python
# detection_engine/deposits_detector.py runs:

account_id = 'AC-001'
balances_30d = [100k, 110k, 120k, ..., 490k, 500k]

# Check: is it trending up?
early_avg = mean(first 10) = 115k
recent_avg = mean(last 10) = 485k
recent_avg > early_avg * 1.2?  → 485k > 138k?  YES ✓

# Signal created:
Signal(
    party_id='PRTY0036',
    signal_type='cash_buildup',
    magnitude=0.85,
    raw_measure={'current_balance': 500000, 'prior_balance': 100000}
)
```

### Step 3: Assembler Looks Up YAML

```python
# datainsights/correlation/hypothesis.py::assemble()

# Detector output (from CSV):
signals = [Signal(signal_type='cash_buildup', ...)]

# Look up in YAML (in memory):
category = _category_for('cash_buildup')
# ↓ reads DOMAINS_CONFIG['deposits']['signals']['cash_buildup']['category']
# ↓ returns 'TREASURY_OPPORTUNITY'

# Check combination rules (from YAML):
for rule in DOMAINS_CONFIG['combinations']:
    if {'cash_buildup'}.issubset(rule['when']):
        # No combination rule matches (need 2+ signals)
        pass

# Final category: TREASURY_OPPORTUNITY (default, no override)
```

### Step 4: Create Recommendation

```python
recommendation = Recommendation(
    prty_id='PRTY0036',
    nba_category='TREASURY_OPPORTUNITY',  # ← From YAML
    hypothesis='...(from YAML)...',        # ← From YAML
    recommended_action='Offer deposit...',  # ← From YAML
    ...
)
```

### Step 5: Render on Dashboard

```python
# datainsights/sinks/fdm_worklist.py

# Recommendation object (built from CSV + YAML):
# → Rendered as row on worklist
# → "PRTY0036 | TREASURY_OPPORTUNITY | Your cleared balances have been..."
```

---

## Where YAML Rules Are Actually Checked Against Data

| Component | File | What It Does |
|---|---|---|
| **Detector** | `detection_engine/*.py` | Reads CSV → generates Signal with `signal_type` |
| **Signal object** | `detection_engine/signal.py` | Carries real data + reference to YAML rule name |
| **Registry loader** | `datainsights/domain_registry.py` | Loads domains_fdm.yaml into memory at startup |
| **HypothesisAssembler** | `datainsights/correlation/hypothesis.py` | Looks up Signal → Category in YAML (now in memory) |
| **Recommendation** | `datainsights/model.py` | Carries the final category from YAML |
| **Sink** | `datainsights/sinks/fdm_worklist.py` | Renders Recommendation → dashboard |

---

## The Execution Flow (Precise Sequence)

```
Startup:
  1. datainsights/domain_registry.py loads config/domains_fdm.yaml
  2. Converted to Python dict in memory: DOMAINS_CONFIG
  3. datainsights/category_registry.py loads config/categories.yaml

Runtime (per client):
  1. Detector reads CSV data
     → Scans accounts.csv, transactions.csv, balances.csv
     → Checks thresholds (from rules.yaml in memory)
     → Generates Signal objects with signal_type='large_incoming_payment' etc.
  
  2. HypothesisAssembler receives signals
     → strongest_signal = max(signals, key=magnitude)
     → category = DOMAINS_CONFIG[domain][signals][signal_type]['category']  ← YAML lookup
     → Check combination rules in DOMAINS_CONFIG['combinations']  ← YAML loop
     → Apply Rule 1 (risk suppression) if needed
  
  3. Create Recommendation object (category from YAML)
  
  4. Sink renders Recommendation → Dashboard
```

---

## Code Links

| What | File | Lines |
|---|---|---|
| Load YAML | `datainsights/domain_registry.py` | ~10-20 |
| Look up signal→category | `datainsights/domain_registry.py::category_for()` | ~40-50 |
| Check combination rules | `datainsights/domain_registry.py::matching_combination()` | ~60-80 |
| Apply all rules to signals | `datainsights/correlation/hypothesis.py::assemble()` | ~217-280 |
| Render to dashboard | `datainsights/sinks/fdm_worklist.py` | ~50-100 |

---

## Key Insight: YAML → Python → Data

```
1. YAML (Static Config)
   domains_fdm.yaml says: "If signal_type='cash_buildup', category='TREASURY_OPPORTUNITY'"

2. Python Registry (In Memory)
   At startup: DOMAINS_CONFIG = yaml.load(domains_fdm.yaml)
   Now it's a dict accessible at runtime

3. Data (Real CSV)
   Detector runs against accounts.csv, balances.csv
   Produces Signal(signal_type='cash_buildup')

4. Lookup (YAML Rules Against Data)
   Assembler receives Signal
   Looks up Signal.signal_type in DOMAINS_CONFIG
   → Retrieves category from YAML

5. Output (Recommendation)
   category='TREASURY_OPPORTUNITY' (from YAML)
   → Sent to dashboard
```

**YAML is NOT executed directly.** It's:
1. Loaded at startup → memory dict
2. Queried at runtime → looked up by signal name
3. Results applied → recommendation built

---

## Why This Design?

### Benefit 1: Configuration, Not Code
```
Change signal→category mapping:
  YAML edit + restart (1 min)
  vs.
  Code edit + compile + test + deploy (30 min)
```

### Benefit 2: Audit Trail
```
YAML file shows:
  "This signal maps to this category"
  "This combination overrides that mapping"
  
If recommendation is wrong, blame the rules, not hidden Python logic
```

### Benefit 3: Non-Engineers Can Edit
```
Business analyst edits domains_fdm.yaml:
  "When we see revenue_pattern_change + facility_maturity_approaching,
   that's growth + renewal opportunity, not just maturity"

Engineer restarts pipeline. Done.
```

---

## Summary

**YAML is instructions.** The actual **logic execution** is:

```
CSV Data → Detector (applies thresholds from rules.yaml) → Signal
                                                              ↓
                                         YAML lookup (via Python registry)
                                              ↓
Signal + YAML → HypothesisAssembler → Category + Hypothesis → Recommendation
```

**The bridge:** Each Signal carries a `signal_type` string (e.g., `'cash_buildup'`). The assembler uses that string to look up rules in `DOMAINS_CONFIG` (the YAML loaded into memory). Result: category from YAML applied to data from CSV.

