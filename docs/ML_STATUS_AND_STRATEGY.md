# Machine Learning in DataInsights: Current Status & Strategy

**TL;DR:** ML is **not actively used in production** right now. The system runs **100% deterministic**. But ML plumbing exists for the future. One experimental ML component (IsolationForest baselining) has been tested but remains **opt-in and off by default**.

---

## What's Running TODAY (Production)

```
Detector thresholds = Static rules in config/rules.yaml
Example: large_incoming_payment = amount > 2.1× rolling 90-day MAD
         ↓
         All detectors use deterministic median/MAD on client history
         ↓
         No learned models, no weights, no training
```

**The dashboard worklist you see** is computed purely from:
1. Deterministic detectors reading canonical data
2. Static combination rules from `config/domains_*.yaml`
3. Deterministic narrative validation (banned-term checks, numeric consistency)

---

## Where ML Could Fit (SLOT E2)

**The one serious gap ML solves:** 

Current approach:
```
large_incoming_payment threshold = 2.1× rolling median for ALL clients
Problem: A £5M transaction is large for a £2M-turnover SME
         but normal for a £500M multinational
Result: False positives on big clients, false negatives on small ones
```

ML solution (SLOT E2 — "Baseline Model"):
```
IsolationForest per client per metric
Learns: "For THIS client, normal is [baseline, range]"
Result: 5M payment is anomaly for SME, normal for multinational
Status: Built (IsolationForestBaseline class exists)
        Tested (compare_baselines.py, scale_evaluation.py)
        NOT ACTIVE (deterministic baseline is default)
```

---

## ML Code That Exists

### 1. **IsolationForestBaseline** (`datainsights/ml/baselines.py`)

**What it does:**
- Trains scikit-learn IsolationForest on each client's transaction history
- Identifies outliers in that history (e.g., one large payment 6 months ago)
- Computes median/MAD on "clean" data (outliers removed)
- Result: Client-specific normal, not global threshold

**Why it's cautious:**
- Fits on client's own unlabelled history only (no ground truth)
- Requires ≥8 observations per client (raises error below that)
- Falls back to deterministic if forest flags nearly everything as outlier
- Per-client, univariate (one metric at a time) — not a cross-client model
- Stateless: re-trains from scratch on every run

**Status in production:** OFF (deterministic median/MAD is default)

### 2. **Evaluation Scripts** (`datainsights/ml/`)

- `compare_baselines.py` — runs both baselines side-by-side on real generated data, one measurement per agreement
- `evaluate_baselines.py` — controlled injection: adds synthetic anomalies, measures detection rate
- `scale_evaluation.py` — same at 300-party/4-year scale, writes run manifest to `var/ml_runs/*.json`

**What the data says (from `docs/current_state.md`):**
> "Keep deterministic as default — disagreement with the challenger isn't yet evidence of improvement on data with no real outcome labels to check against."

**Translation:** IsolationForest finds different clients as anomalous, but without ground truth RM feedback, we can't say which is "correct."

### 3. **Policy & Registry** (`datainsights/ml/policy.py`, `model_registry.py`)

Framework for future multi-model scenarios:
- Centralized policy: "Which baseline to use for which detector/entity?"
- Per-schema configuration: "For FDM, use IsolationForest. For legacy, use deterministic."
- Per-detector opt-in: Config in `config/ml_policy.yaml`

**Status:** Plumbing exists, not used yet.

---

## Why No Real ML Yet? (The Three Requirements)

From `docs/current_state.md`:

```
ML tab says: "No ML challenger is eligible"
Reasons:
  1. Too few entities (92 < 200 minimum for multi-client model)
  2. Zero RM outcome labels (0 < 100 minimum for supervised learning)
  3. No ground truth for validation
```

**Translation:**
- **92 entities:** Enough for per-client IsolationForest (univariate), NOT enough for a cross-client propensity model
- **0 labels:** Can't train a supervised model. Can't answer "Did the detector output match RM's response?"
- **No validation:** Can't verify "Is IsolationForest actually better than deterministic?"

---

## The Four ML Slots (Decision Record)

The architecture defines where ML *could* plug in:

| Slot | Purpose | Type | Status |
|---|---|---|---|
| **E1: Normaliser** | Baseline of what "normal" is per client | Statistical | ✅ Built (deterministic), tested (IsolationForest as challenger) |
| **E2: Baselines** ★ | Client-specific anomaly thresholds | ML | ✅ Built (IsolationForest), opt-in, NOT ACTIVE |
| **E3: Exposure Qualifier** | "Does this external event apply to this client?" | Supervised | ❌ Future (needs labels + Trucost data) |
| **E4: Propensity** | "Will RM act on this recommendation?" | Supervised | 🚫 Out of scope — Pega owns this |

**★ E2 is the highest-value ML opportunity** — client-specific baselines solve the "£5M for SME vs multinational" problem without needing labels.

---

## Why Not Use E2 (IsolationForest) Now?

**Two reasons:**

### 1. No Real Data to Validate Against

The detector output changes (some clients flag/unflag), but **without RM feedback**, we don't know if it's better:

```
Deterministic: Flags 12 clients as "large payment"
IsolationForest: Flags 8 clients

Which is correct? ??? (no labels to check against)
```

### 2. Measured Cost Isn't Worth Unproven Benefit

IsolationForest per-client fit adds ~0.7s per agreement (after tuning `n_estimators`). If the output doesn't improve accuracy, that's wasted CPU.

---

## The Path Forward (When Labels Arrive)

**Phase 1: Gather RM Feedback**
```
Dashboard records every RM response:
- "Engaged" (RM contacted client)
- "Not Appropriate"
- "Remind Me Later"
→ Feeds into datainsights/rm_feedback.py
```

**Phase 2: Run Backtests**
```
python -m datainsights.backtest --as_of_date 2026-06-01
→ Replays detector output as-of June 1
→ Joins with RM feedback after June 1
→ Measures: "How many flagged clients actually engaged?"
```

**Phase 3: Train Propensity Model (E4)**
```
Input: (client, category, features) + RM response label
Output: P(RM engages | features)
Status: Coordinate with Pega, don't build competing ranker
```

---

## What's Actually Running on Your Dashboard

```
✅ Deterministic detectors (config/rules.yaml thresholds)
✅ Static combination rules (config/domains_*.yaml)
✅ Deterministic validation (banned-term checks, numeric consistency)
❌ IsolationForest baseline (opt-in, not active)
❌ Supervised models (no labels yet)
❌ RM-engagement propensity (Pega's job)
```

**Result:** Every recommendation is fully traceable to:
- CSV data (source)
- Detector rule (2.1× MAD threshold)
- Combination rule (which signals together mean FINANCING_NEED)
- Evidence (which transactions/balances triggered it)

**No "black box" predictions.** No learned weights to hide behind.

---

## How to Experiment with E2

If you want to test IsolationForest locally:

```bash
# Run side-by-side comparison
python -m datainsights.ml.compare_baselines

# Inject synthetic anomalies, measure detection
python -m datainsights.ml.evaluate_baselines

# Scale test: 300 parties, 4 years history
python -m datainsights.ml.scale_evaluation --also-holdout
```

None of these affect the production dashboard. They just tell you: "If we turned on IsolationForest, would it catch different clients?" 

Answer so far: "Yes, different clients, but no evidence it's better without labels."

---

## The Honest Assessment

From the code docstring (`datainsights/ml/baselines.py`):

> "Whether it is actually better than the deterministic benchmark is an empirical question answered by datainsights/ml/evaluate_baselines.py, not by this docstring. The deterministic baseline stays the production default until that comparison says otherwise on real data."

**Translation:** ML exists as an option, tested, but production runs deterministic until we have proof it's better.

---

## Summary

| Aspect | Status |
|---|---|
| **Is ML used in production?** | No — deterministic only |
| **Does ML code exist?** | Yes — IsolationForest baseline built & tested |
| **Why not active?** | No RM outcome labels to validate against |
| **What would unlock it?** | 100+ RM engagement records (from dashboard feedback) |
| **Best ML opportunity?** | E2 (client-specific baselines) — solves the scale problem |
| **Coordination requirement?** | E4 (propensity) lives in Pega, not here |

