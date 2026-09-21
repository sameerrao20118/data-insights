# All Detectors & Signals: How Multiple Records Per Client Are Considered

You have **11 detectors** running in parallel. Each produces signals. Multiple signals per client are **combined** using the logic you already saw. Let me show you all of them.

---

## The 11 Detectors (Complete List)

```
detection_engine/
├── cash_buildup.py                    ✅ Deposits domain
├── large_incoming_payment.py           ✅ Deposits domain
├── dormancy.py                         ✅ Deposits domain
├── revenue_pattern_change.py           ✅ Deposits domain
├── facility_utilization_spike.py       ✅ Lending domain
├── facility_maturity_approaching.py    ✅ Lending domain
├── fixed_rate_expiry.py                ✅ Lending domain
├── collateral_coverage_drop.py         ✅ Lending domain
├── rating_downgrade.py                 ✅ Risk domain
├── pd_migration.py                     ⚠️  Risk domain (not wired yet)
└── signal.py                           (Signal data class)
```

---

## How Multiple Records Per Client Are Considered

### Key Principle: **Aggregation at Detector Level**

Each detector **aggregates all records for a client** before creating a signal.

### Example 1: Cash Buildup (Multiple Balance Records)

```python
# detection_engine/cash_buildup.py

def detect(accounts: pd.DataFrame, balances: pd.DataFrame) -> list[Signal]:
    """
    Input: balances has 30+ rows per account (one row per day)
    Task: Aggregate into ONE signal per account per client
    """
    signals = []
    
    for account_id in accounts['account_id']:
        # Get ALL balance records for this account (30+ days)
        account_balances = balances[balances['account_id'] == account_id]
        
        # Aggregate: Compare first 10 days to last 10 days
        first_10_days_avg = account_balances.head(10)['closing_balance'].mean()
        last_10_days_avg = account_balances.tail(10)['closing_balance'].mean()
        
        # Check: is trend up?
        if last_10_days_avg > first_10_days_avg * 1.2:
            # ONE signal per account (aggregated from 30+ records)
            signals.append(Signal(
                party_id=client_id,
                signal_type='cash_buildup',
                magnitude=0.85,
                raw_measure={
                    'current_balance': float(account_balances.iloc[-1]['closing_balance']),
                    'prior_balance': float(account_balances.iloc[0]['closing_balance']),
                    'days_reviewed': len(account_balances),
                    'trend': 'upward'
                }
            ))
    
    return signals
```

**Key:** 30 daily balance records → 1 signal per account, aggregated.

### Example 2: Large Incoming Payment (Multiple Transaction Records)

```python
# detection_engine/large_incoming_payment.py

def detect(accounts: pd.DataFrame, transactions: pd.DataFrame, rules: dict) -> list[Signal]:
    """
    Input: transactions has 100+ rows per account (one per transaction)
    Task: Aggregate into ONE signal per account per client
    """
    signals = []
    
    for account_id in accounts['account_id']:
        # Get ALL transactions for this account
        account_txns = transactions[transactions['account_id'] == account_id]
        
        # Filter to INFLOWS only (credit transactions)
        inflows = account_txns[account_txns['direction'] == 'credit']['amount']
        
        # Aggregate: Compute rolling baseline (90-day median)
        baseline_median = inflows.quantile(0.5)
        baseline_mad = (inflows - baseline_median).abs().median()
        
        # Threshold from rules.yaml: 2.1× MAD
        threshold = baseline_median + (2.1 * baseline_mad)
        
        # Check: any recent transaction > threshold?
        recent_txns = account_txns.tail(10)  # Last 10 transactions
        large_txns = recent_txns[recent_txns['amount'] > threshold]
        
        if not large_txns.empty:
            # ONE signal per account (from 100+ transactions aggregated)
            largest = large_txns.iloc[0]
            signals.append(Signal(
                party_id=client_id,
                signal_type='large_incoming_payment',
                magnitude=0.95,  # High confidence
                raw_measure={
                    'flagged_amount': float(largest['amount']),
                    'baseline_median': float(baseline_median),
                    'baseline_mad': float(baseline_mad),
                    'num_inflows_reviewed': len(inflows),
                    'days_reviewed': (recent_txns['booking_date'].max() - recent_txns['booking_date'].min()).days,
                    'currency': 'EUR'
                }
            ))
    
    return signals
```

**Key:** 100+ transactions → 1 signal per account, aggregated over 90-day window.

---

## All 11 Detectors Explained

### Domain: DEPOSITS (4 Detectors)

#### 1. **cash_buildup** 
- **Input:** Multiple daily balance records per account
- **Logic:** Compare avg balance last 10 days vs first 10 days
- **Output:** Signal if trend UP > 20%
- **Category:** TREASURY_OPPORTUNITY
- **Revenue:** Fee/spread on deposited surplus
- **Code:** `detection_engine/cash_buildup.py`

#### 2. **large_incoming_payment**
- **Input:** Multiple transaction records per account (90+ days)
- **Logic:** Compute rolling median/MAD of inflows, flag if recent txn > 2.1× MAD
- **Output:** Signal if large payment detected
- **Category:** TREASURY_OPPORTUNITY (or FINANCING_NEED if combined)
- **Revenue:** Deposit or working capital product
- **Code:** `detection_engine/large_incoming_payment.py`

#### 3. **dormancy**
- **Input:** All transactions for account (full history)
- **Logic:** Count days since last transaction
- **Output:** Signal if no activity > 90 days
- **Category:** ADVISORY_ONLY
- **Revenue:** Relationship retention, not direct
- **Code:** `detection_engine/dormancy.py`

#### 4. **revenue_pattern_change**
- **Input:** Multiple transaction records per account (60-90 days)
- **Logic:** Compare avg inflow amount month-1 vs month-2
- **Output:** Signal if inflows UP > 30% month-over-month
- **Category:** FINANCING_NEED
- **Revenue:** Growth financing, working capital
- **Code:** `detection_engine/revenue_pattern_change.py`

### Domain: LENDING (4 Detectors)

#### 5. **facility_utilization_spike**
- **Input:** Multiple daily balance records per facility (with limit)
- **Logic:** Compute utilization = drawn/limit
- **Output:** Signal if utilization > 85%
- **Category:** FINANCING_NEED
- **Revenue:** Facility limit increase, interest income
- **Code:** `detection_engine/facility_utilization_spike.py`

#### 6. **facility_maturity_approaching**
- **Input:** Agreement/facility records (one per facility)
- **Logic:** Check days to close_date
- **Output:** Signal if close_date < 90 days away
- **Category:** FINANCING_NEED (renewal)
- **Revenue:** Renewal fees, interest
- **Code:** `detection_engine/facility_maturity_approaching.py`

#### 7. **fixed_rate_expiry**
- **Input:** Mortgage/fixed-rate facility records
- **Logic:** Check days to fixed_rate_end_date
- **Output:** Signal if expiry < 90 days away
- **Category:** TREASURY_OPPORTUNITY or HEDGING_NEED
- **Revenue:** Refi fees, hedge fees
- **Code:** `detection_engine/fixed_rate_expiry.py`

#### 8. **collateral_coverage_drop** ⚠️
- **Input:** Multiple collateral valuation records over time
- **Logic:** Compare current collateral value vs facility limit
- **Output:** Signal if coverage < 80% (or configurable threshold)
- **Category:** RISK_REVIEW
- **Revenue:** Defensive, protects existing book
- **Code:** `detection_engine/collateral_coverage_drop.py`
- **Status:** Not wired yet (collateral not in legacy CSV)

### Domain: RISK (2 Detectors)

#### 9. **rating_downgrade**
- **Input:** PARTY risk grade (one per party)
- **Logic:** Compare current RSK_GRD_CD vs baseline
- **Output:** Signal if grade migrated down
- **Category:** RISK_REVIEW
- **Revenue:** Defensive, credit monitoring
- **Code:** `detection_engine/rating_downgrade.py`
- **Status:** Wired but risk_ratings.csv not in legacy contract yet

#### 10. **pd_migration**
- **Input:** PD model outputs (LC_MODEL_DATA, ML_MODEL_DATA)
- **Logic:** Compare current PD vs baseline
- **Output:** Signal if PD migrated up (risk increased)
- **Category:** RISK_REVIEW or FINANCING_NEED
- **Revenue:** Defensive or early financing alert
- **Code:** `detection_engine/pd_migration.py`
- **Status:** NOT WIRED YET (PD data not in generated dataset)

#### 11. **signal.py**
- **Not a detector, but defines Signal dataclass**
- Carries: signal_type, magnitude, raw_measure, party_id, domain, evidence_ref
- Used by all detectors

---

## How Multiple Signals Per Client Are Aggregated

### Real Example: PRTY0036 at Runtime

```
Detector 1: cash_buildup
  Input: 30 daily balance records for AC-001
  Output: Signal(signal_type='cash_buildup', magnitude=0.85)

Detector 2: large_incoming_payment
  Input: 100 transaction records for AC-001 (last 90 days)
  Output: Signal(signal_type='large_incoming_payment', magnitude=0.95)

Detector 3: revenue_pattern_change
  Input: 60 transaction records for AC-001 (60-90 days)
  Output: Signal(signal_type='revenue_pattern_change', magnitude=0.88)

Detector 4: facility_utilization_spike
  Input: 30 daily balance records for AC-002 (credit facility)
  Output: Signal(signal_type='facility_utilization_spike', magnitude=0.92)

Detector 5-11: (no signals for this client)

RESULT: signals = [
    Signal(type='cash_buildup', magnitude=0.85),
    Signal(type='large_incoming_payment', magnitude=0.95),
    Signal(type='revenue_pattern_change', magnitude=0.88),
    Signal(type='facility_utilization_spike', magnitude=0.92),
]
```

### HypothesisAssembler Combines Them

```python
# All 4 signals for PRTY0036
signals = [cash_buildup, large_incoming_payment, revenue_pattern_change, facility_utilization_spike]

# Step 1: Find strongest
strongest = facility_utilization_spike (0.92 magnitude)

# Step 2: Get default category
category = FINANCING_NEED

# Step 3: Check combination rules
# Rule: "growth_outrunning_working_capital"
# when: [cash_buildup, facility_utilization_spike]  ← Both present!
# category: FINANCING_NEED
# hypothesis: "Deposits building while facility drawn harder..."

# RESULT: Final category = FINANCING_NEED (overrides individual defaults)
# RESULT: Hypothesis explains WHY: growth outrunning working capital
# RESULT: Size based on facility_utilization_spike amount
```

---

## The Revenue Opportunity Question: Are All Scenarios Covered?

### Current Coverage (What We Have)

✅ **Deposits signals (4):**
- Balance building (idle cash)
- Large payment (temporary surplus)
- Revenue growth (structural change)
- Dormancy (retention risk)

✅ **Lending signals (4):**
- Facility drawn hard (headroom constrained)
- Facility maturing (renewal timing)
- Fixed rate expiring (refi/hedge timing)
- Collateral coverage falling (risk)

✅ **Risk signals (2):**
- Rating downgrade (credit deterioration)
- PD migration (model-based risk)

### Scenarios NOT YET DETECTED (Edge Cases)

**Cross-product scenarios:**
- ❌ Customer has money on one account, needs credit on another → **Treasury-to-Lending cross-sell**
- ❌ Multiple accounts with related inflows (subsidiary consolidation) → **Group treasury opportunity**
- ❌ Account A has recurring expense, Account B has sporadic income → **Working capital hedging**

**Temporal patterns:**
- ❌ Predictable spending pattern (e.g., monthly rent) → **Dynamic payment solution**
- ❌ Seasonal revenue swings → **Seasonal financing**
- ❌ Transaction velocity changing (faster/slower pace) → **Liquidity stress signal**

**Relationship signals:**
- ❌ Client inactivity but peer in same group is active → **Concentration risk**
- ❌ Facility utilization rising while collateral falling → **Margin call risk**

**External correlation:**
- ❌ Competitor in same sector faces stress → **Pre-emptive positioning**
- ❌ Commodity price moving → **Hedging opportunity**

---

## Why We Don't Have All Scenarios (Three Reasons)

### Reason 1: Deterministic Design is Intentional

**Philosophy:** "Don't invent signals from thin air"

```python
# Current approach: Only detect what we can trace to real data
if balance_trending_up:
    signal = cash_buildup  # ← Traceable to accounts.csv

# NOT: Speculative signal
# if customer_appears_successful:
#     signal = likely_to_expand  # ← Too vague, unprovable
```

**Why:** Detectors must be auditable. RM needs to verify: "Why did this signal fire?" If the detector can't point to a transaction or balance record, it's a guess, not a detection.

### Reason 2: Ground Truth Validation is Missing

```
Before adding a new detector, we'd need:
  1. Historical data on that pattern
  2. RM feedback: "Did this pattern matter?"
  3. Backtest: "Would detecting this pattern have improved outcomes?"

Without ground truth labels, we can't validate if a new detector is accurate.
```

**Current status:** Zero RM outcome labels. No way to say "This detector was right 87% of the time."

### Reason 3: Small Dataset (Synthetic, Not Real)

- 606 accounts (too small for statistical patterns)
- 4 years history (synthetic, not market-tested)
- No real RM feedback

Real bank data: millions of accounts, decades of history, RM feedback → can find patterns reliably.

---

## How to Discover New Revenue Scenarios (Your Questions)

### Approach 1: Add a Detector (Deterministic Route)

If you spot a pattern in the data:

```
1. Identify it in real data: "Clients with recurring expenses get 20% cost savings with payment automation"

2. Build detector:
   detection_engine/recurring_payment_pattern.py
   Logic: Count same-amount transactions on same day of month
   Output: Signal if pattern exists

3. Update config:
   config/domains_fdm.yaml → Map to category (TREASURY_OPPORTUNITY)

4. Run detector
   New signal type available for combination rules

5. Validate: RM feedback needed
   "Did clients with this pattern actually engage?"
```

### Approach 2: Exploratory Data Analysis (Off-Path)

If you want to find hidden patterns:

```python
# Ad-hoc analysis (NOT in detector pipeline):
import pandas as pd

accounts = pd.read_csv('data_generator/output/accounts.csv')
transactions = pd.read_csv('data_generator/output/transactions.csv')
balances = pd.read_csv('data_generator/output/balances.csv')

# Q: Which clients have both building balance AND rising expenses?
# Q: Which clients are in industries with seasonal patterns?
# Q: Which clients have multiple accounts with different patterns?

# Find patterns → propose new detectors
# Build detectors → validate against outcomes
```

### Approach 3: ML-Based Pattern Discovery (Future, E4)

```
When ground truth labels arrive:
  Use ML to find: "Which combinations of features predict RM engagement?"
  
Example: Isolation Forest finds anomalies ML can't
  - Detectors find: "Balance is high"
  - ML notices: "For THIS client type, high + foreign_currency = hedging interest"
  
But only IF labels exist to validate.
```

---

## Edge Cases & Risks: What About Exploitation?

### Risk 1: **False Positives** (Detector fires wrongly)

**Example:** Facility utilization hits 85% because of a one-time project.
```
Detector: "Client needs headroom!"
Reality: "They're managing fine, just a spike."

Mitigation: 
  - Cooldown periods (don't re-fire within 30 days)
  - Combination rules (single spike = weaker signal)
  - Confidence scoring (single signal = 0.7, multiple domains = 0.95)
```

### Risk 2: **Missed Patterns** (Detector doesn't fire)

**Example:** Client consolidating to competitor (dark signal).
```
Detector: No dormancy signal (still some activity).
Reality: Moving 80% of business to rival.

Mitigation:
  - frequency_change detector (count of txns down)
  - relationship_concentration detector (deposits down while facility unchanged)
  - (But not yet built, no ground truth to validate)
```

### Risk 3: **Gaming by Sophisticated Clients**

**Example:** Client manipulates signals.
```
Scenario: Client with large facility draws/repays strategically to stay under 85%.
  
Mitigation:
  - Duration check: signal fires if >3 days at high utilization
  - Trend analysis: upward creep, not spike
  - RM review: investigator agent flags "suspicious" patterns
```

### Risk 4: **Exogenous Event Manipulation** (Not Yet In System)

**Example:** Tender award signals manufacturing need.
```
Scenario: Client mentions fake tender to get financing offer.

Mitigation:
  - exogenous_exposure_qualifier (does client actually have this industry code?)
  - external_event_source validation (TED API, not hearsay)
  - RM sign-off required for exogenous correlation
```

---

## Summary: Current vs. Potential

| Aspect | Current | Potential |
|--------|---------|-----------|
| **Detectors** | 9 wired (11 built) | 15-20 (multi-account, temporal, external) |
| **Signals per client** | 0-4 typically | 0-8+ (more granular patterns) |
| **Combination rules** | 5 rules | 20+ (cross-domain scenarios) |
| **Validation** | Deterministic logic only | ML-based + rule-based hybrid |
| **Risk handling** | High-risk flag suppresses revenue | Explicit risk-commerce tradeoff matrix |
| **Edge cases** | Handled via cooldowns, thresholds | Handled via investigator agent (A2) |
| **Ground truth** | None, can't validate accuracy | Needed: 100+ RM outcomes per signal type |

---

## What Stops New Scenarios: Ground Truth Bottleneck

You found the real constraint:

**Current state:**
```
Detector logic ✅ (can write any logic)
YAML config ✅ (can add any rule)
Data access ✅ (CSVs fully visible)
Validation ❌ (NO RM feedback = can't know if detector is right)
```

**If RM starts recording outcomes:**
```
RM response: "Customer Engaged" / "Not Appropriate" / "Remind Me Later"
             ↓
Backtest: Run detector output vs. actual RM outcome
             ↓
Learn: "Dormancy detector was 65% accurate, facility_utilization_spike was 88%"
             ↓
Safe to train ML or add new detectors with confidence
```

**Then edge cases become discoverable via:**
1. RM feedback analysis ("These 3 signal combos were never acted on")
2. ML feature importance ("These 5 features predict engagement best")
3. Error analysis ("These 2 signal patterns predicted wrong 15% of time")

---

## How to Exploit Opportunities (If You Had Labels)

```python
# Hypothetical: 6 months of RM feedback data exists
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

feedback = pd.read_csv('rm_feedback.csv')  # engagement = Y/N
detector_output = pd.read_csv('detector_signals.csv')  # signal_type, magnitude, etc.

# Q1: Which signal combinations predict engagement?
rf = RandomForestClassifier()
rf.fit(detector_output[['cash_buildup', 'large_payment', 'revenue_growth', ...]], 
       feedback['rm_engaged'])
print(rf.feature_importances_)  # Discover which matter most

# Q2: Which clients were NOT flagged but RM engaged anyway?
false_negatives = feedback[(feedback['flagged'] == False) & (feedback['engaged'] == True)]
# Analyze their patterns → new detector opportunity

# Q3: Which signal combinations have highest ROI?
by_combination = detector_output.groupby(['signal_combo']).agg({
    'engagement_rate': 'mean',
    'revenue_generated': 'sum'
})
print(by_combination.sort_values('revenue_generated', ascending=False))
# → Prioritize highest-ROI combos
```

But without the feedback, this is all blocked.

---

## Your Edge Case Question: Yes, There Are Gaps

**Confirmed:**
- ✅ 9 detectors cover main scenarios (deposits, lending, risk)
- ✅ Combination rules handle cross-domain insights
- ❌ Multi-account patterns (subsidiary relationships) NOT detected
- ❌ Temporal patterns (seasonality, velocity changes) NOT detected
- ❌ Relationship anomalies (quiet key account) NOT detected

**To unlock them:** Ground truth labels. Once RMs start recording outcomes, every gap becomes visible in the data.

