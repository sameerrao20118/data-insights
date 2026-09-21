# Streamlit Dashboard: User Guide & Navigation

Most users interact with DataInsights through the **Streamlit dashboard**, not the command line. This guide shows you how to navigate it, what each section does, and how to accomplish common tasks.

---

## Quick Start: Launch the Dashboard

```bash
# Terminal command (one-time)
streamlit run dashboard/app.py

# Output: Local URL is http://localhost:8501
# Your browser opens automatically
```

**Requirements:**
- Python 3.9+
- Dependencies installed: `pip install -r requirements.txt`
- Local Ollama running (optional, for LLM narratives): `ollama serve`

---

## Dashboard Layout

```
┌─────────────────────────────────────────────────────┐
│                                                     │
│  SIDEBAR (left)          │  MAIN CONTENT (right)    │
│  ├─ DataInsights header  │  ├─ Page title          │
│  ├─ Ollama status        │  ├─ Filters/controls    │
│  ├─ Current user         │  ├─ Main content        │
│  ├─ Section menu (radio) │  ├─ Tables/charts       │
│  ├─ Divider             │  └─ Recommendations     │
│  └─ Disclaimer          │                         │
│     (synthetic data)     │                         │
│                          │                         │
└─────────────────────────────────────────────────────┘
```

**Sidebar:** Always visible, left side. Radio buttons select sections.
**Main:** Changes based on selected section.

---

## The 10 Dashboard Sections (In Sidebar)

### 1. **Overview**
**What it does:** High-level introduction to DataInsights
- Explains problem: "How to detect revenue opportunities automatically"
- Shows the pipeline flow
- Links to reference docs

**When to use:** First time visiting the dashboard, or need a refresh on what this system does.

**Key insight:** "All recommendations are deterministic — traceable to real data, not ML black boxes."

---

### 2. **Explore a Source**
**What it does:** Browse raw data after binding is applied

**How to use:**
1. Select a concept (Account, Transaction, Balance, Party, etc.)
2. Filter by party_id (customer ID)
3. See all canonical-name columns (what detector uses)
4. Inspect raw values (for debugging)

**Example flow:**
- Concept: Account
- Filter: PRTY0036
- See: party_id, product_code, opening_balance, etc.
- Verify: "This is the data my detector read"

**When to use:** 
- Debugging: "Why did this signal not fire?"
- Validation: "Is binding working correctly?"
- Curiosity: "What data does detector actually see?"

**Key insight:** "This shows canonical data AFTER binding. Physical CSV columns are renamed here."

---

### 3. **Prepare for the Client Call**
**What it does:** The main worklist — recommendations ranked by priority

**How to use:**
1. Left sidebar: Filter by category (FINANCING_NEED, TREASURY_OPPORTUNITY, etc.)
2. Left sidebar: Filter by confidence (0-1 scale)
3. Main table: Click a client row
4. Right panel: Full recommendation details

**What each recommendation shows:**
- **Recommendation ID:** Stable across reruns (for RM feedback tracking)
- **Client:** Party ID + segment + country
- **Category:** FINANCING_NEED / TREASURY_OPPORTUNITY / RISK_REVIEW / etc.
- **Why now:** Business reason (e.g., "Balance climbing steadily")
- **Hypothesis:** Full narrative explanation
- **Recommended action:** Specific offer (sized, in currency)
- **Evidence:** Which signals confirmed it + confidence
- **Confirmed domains:** How many domains agree (deposits + lending = stronger)

**Key columns:**
- **RM to contact:** Who owns the relationship
- **Priority:** Confidence × revenue potential
- **Revenue estimate:** Illustrative sizing
- **Status:** Customer engaged / Not appropriate / Remind me later

**When to use:** Daily — this is where RMs see their work queue.

**Key insight:** "Every recommendation is traceable: click into 'Evidence' to see which CSV records triggered it."

---

### 4. **Digests**
**What it does:** Summarized worklist by segment (SME, Mid-Corp, Large-Corp, etc.)

**How to use:**
1. Select segment
2. See aggregated stats: total opportunities, by category, by revenue
3. Drill into segment to see individual clients

**When to use:** 
- Portfolio view (RM manager looking at their team's book)
- Segment strategy (Which segment has most opportunity?)
- Trend monitoring (Is this month stronger than last month?)

**Key insight:** "Segment-level rollup of the main worklist."

---

### 5. **How It Works**
**What it does:** Educational walkthrough of the system

**Sections:**
- The pipeline (data → detector → signal → recommendation)
- The three-layer schema (physical → semantic → binding)
- Signal combination logic
- Risk suppression rules

**When to use:** 
- Understanding the system
- Training new analysts
- Explaining to stakeholders

**Key insight:** "If you don't understand a recommendation, read this section first."

---

### 6. **Verification Proofs**
**What it does:** Demonstrates system correctness

**Shows:**
- "Deterministic pipeline produces same output every run"
- "Binding works: same detector runs on legacy and FDM schemas unchanged"
- "No data leakage: ground-truth labels never seen by detector"
- "Confidence scoring is decomposable (traceable to individual signals)"

**When to use:**
- Audit/compliance: "Is this system reliable?"
- Technical review: "Are the safeguards real?"
- Before deployment: "Can we trust the output?"

**Key insight:** "Every claim has a test. Correctness is verified, not assumed."

---

### 7. **ML Opportunities**
**What it does:** Shows ML readiness and baseline comparisons

**Sections:**
- Current ML status (deterministic today, no active ML)
- Isolation Forest baseline comparison (opt-in alternative to median/MAD)
- PD model readiness (when historical labels exist)
- ML metrics vs. deterministic (once validation data available)

**When to use:**
- Considering ML adoption
- Understanding current limitations
- Evaluating IsolationForest baseline

**Key insight:** "ML is opt-in, not required. Deterministic baselines are auditable."

---

### 8. **Onboard a New Schema**
**What it does:** Self-service schema onboarding

**How to use:**
1. Upload CSV files (accounts, transactions, balances)
2. System auto-detects columns and types
3. Generate entities.yaml (with flagged choices for you to confirm)
4. Generate semantic_model.yaml (canonical field names)
5. Review and download config files

**When to use:** 
- Adding new data source (Snowflake, AWS, local database)
- Testing with your own data
- Onboarding to production

**Key insight:** "This automates 80% of Stage 2 setup. You still review and confirm choices."

---

### 9. **Technique Reference**
**What it does:** Reference docs within the dashboard

**Contains:**
- Link to decision_record.md (design philosophy)
- Link to architecture.md (pipeline deep dive)
- Link to all 10+ reference docs
- FDM mapping reference (canonical concepts)

**When to use:** Need docs without leaving dashboard.

**Key insight:** "One-click access to all reference material."

---

### 10. **System Status**
**What it does:** Health & diagnostics

**Shows:**
- Last run timestamp
- Dataset loaded (which CSV files)
- Number of clients evaluated
- Detectors wired + signals produced
- LLM/Ollama status
- Database/cache status
- Any warnings or blockers

**When to use:**
- Debugging: "Why aren't signals firing?"
- Operations: "When did pipeline last run?"
- Health check: "Is system ready?"

**Key insight:** "If something seems wrong, check Status first."

---

## Common User Tasks (Step-by-Step)

### Task 1: Find Recommendations for a Specific Customer

```
1. Go to: "Prepare for the Client Call" (main worklist)
2. Sidebar: Search/filter by party_id
3. See: All recommendations for that customer
4. Click: Customer row for details
5. Read: "Why now" + "Hypothesis" + "Recommended action"
```

---

### Task 2: Understand Why a Signal Fired

```
1. Go to: "Prepare for the Client Call"
2. Click: Customer row → expand "Evidence"
3. See: Which signals triggered + which domains confirmed
4. Go to: "Explore a Source"
5. Concept: Select the type (Account, Transaction, Balance)
6. Filter: party_id
7. Inspect: Raw values detector used
8. Go to: "How It Works" → "Signal → Category Logic"
9. Read: How that signal maps to category
```

---

### Task 3: Check System Health

```
1. Go to: "System Status"
2. Read: Last run time, dataset, detectors active
3. Check: Ollama reachable? (shown in sidebar)
4. If blocked: See "Warnings" section
```

---

### Task 4: View High-Level Portfolio

```
1. Go to: "Digests"
2. Select: Segment (SME, Mid-Corp, etc.)
3. See: Aggregated stats + breakdown by category
4. Drill into: Segment for individual clients
```

---

### Task 5: Compare Deterministic vs. ML Baseline

```
1. Go to: "ML Opportunities"
2. See: "Baseline Comparison" chart
3. Read: "Deterministic vs. IsolationForest"
4. Understand: Why ML isn't active yet (needs labels)
```

---

### Task 6: Add Your Own Data

```
1. Go to: "Onboard a New Schema"
2. Upload: CSV files (accounts, transactions, balances)
3. Review: Auto-detected columns
4. Confirm: Field types and required columns
5. Download: Generated config files
6. Follow: Stage 2 setup docs (copy configs to /config)
7. Rerun: Pipeline with new profile
```

---

## Dashboard Controls & Filters

### Main Worklist ("Prepare for the Client Call") Filters

**Left sidebar (always visible):**
- **Category filter:** Pick one or more (FINANCING_NEED, TREASURY_OPPORTUNITY, RISK_REVIEW, etc.)
- **Confidence range:** Slider (0.0 to 1.0)
- **Segment filter:** SME / Mid-Corp / Large-Corp
- **Search:** Customer ID (party_id)

**Column controls (in table):**
- Sort by any column (click column header)
- Hide/show columns (menu icon)

---

## Reading a Recommendation Card

When you click a client row, you see:

```
┌──────────────────────────────┐
│ PRTY0036 - Manufacturing, ES │
│ Account: AC-001              │
├──────────────────────────────┤
│ Category: FINANCING_NEED     │
│ Confidence: 0.95 (★★★★★)     │
├──────────────────────────────┤
│ Why now:                     │
│   Winning a public tender    │
│   creates a cash-flow gap    │
├──────────────────────────────┤
│ Hypothesis:                  │
│   The client won a contract  │
│   requiring working capital  │
│   before payment arrives...  │
├──────────────────────────────┤
│ Recommended action:          │
│   Offer €800,000 working     │
│   capital financing to       │
│   bridge delivery→payment    │
├──────────────────────────────┤
│ Evidence (confirming domains)│
│   • large_incoming_payment   │
│   • revenue_pattern_change   │
│   • facility_maturity (soon) │
│   Confirmed by: 2 domains    │
├──────────────────────────────┤
│ Sizing basis: illustrative   │
│ Recommendation ID: abc123    │
└──────────────────────────────┘
```

**Each field explains itself:**
- **Category:** What revenue product type
- **Confidence:** How many signals + domains agree
- **Why now:** Urgency/timing
- **Hypothesis:** Full story (what narrative says)
- **Recommended action:** What to offer + sized amount
- **Evidence:** Which signals triggered it
- **Sizing basis:** How offer was computed (transparent, never black box)

---

## Sidebar Status Indicators

**At top of sidebar:**

```
DataInsights
Commercial/institutional NBA-EBM proof of concept

Local Ollama: 🟢 reachable
  (or 🔴 not reachable → falls back to deterministic template)

Signed in: demo_supervisor (supervisor; whole book)
  (or: local_dev; 3 RM codes)

Synthetic data only — no real client, transaction, or event data
```

**What it means:**
- 🟢 Ollama reachable = LLM narratives will run
- 🔴 Ollama not reachable = Narratives use template (still deterministic)
- User role determines visibility (supervisor sees all; RM sees only their accounts)

---

## Tips for Effective Use

### 1. **Trust the Evidence**
Every recommendation is traceable. If you don't believe a recommendation:
- Go to "Explore a Source" and inspect the raw data
- Go to "How It Works" and understand the signal logic
- Check System Status for any warnings

### 2. **Use Confidence Score**
Confidence is **decomposable:**
- Single signal from one domain = 0.5-0.7 confidence
- Two signals from different domains = 0.8+ confidence
- Exogenous event alignment adds +1 to score
- High-risk flag suppresses revenue categories

**Use confidence to set your engagement bar.**

### 3. **Filter Before Sorting**
Sidebar filters run first, main table sorts result:
1. Apply category + confidence filters
2. Sort by priority/confidence/revenue
3. Scan top 10-20

### 4. **Check "Confirmed Domains"**
Recommendations confirmed by multiple domains (deposits + lending) are stronger than single-domain signals. Use this to prioritize.

### 5. **Understand Sizing Basis**
Every sized offer shows how it was computed:
- `balance_buildup_amount_illustrative` = based on recent balance increase
- `limit_increase_to_70pct_utilization_illustrative` = facility sizing to 70% utilization
- `pct_of_event_value_illustrative` = % of external event (e.g., tender award)

**"Illustrative" means: sized from available data, not calibrated to real margins yet.**

---

## When to Use Each Section

| Task | Section |
|------|---------|
| Review worklist for today | Prepare for the Client Call |
| See portfolio view | Digests |
| Debug why signal fired | Explore a Source + How It Works |
| Understand system | How It Works + Verification Proofs |
| Check health | System Status |
| Evaluate ML | ML Opportunities |
| Add new data source | Onboard a New Schema |
| Find reference docs | Technique Reference |

---

## Keyboard Shortcuts

Most browser shortcuts work in Streamlit:

- **Cmd/Ctrl + F:** Search within page (works in tables)
- **Cmd/Ctrl + P:** Print (save as PDF)
- **Tab / Shift+Tab:** Navigate between controls
- **Enter:** Select radio button / collapse section

Streamlit-specific:
- **R:** Rerun the app (reload data)
- **C:** Clear cache (forces re-fetch)
- **V:** View source (debug Streamlit settings)

---

## Troubleshooting in Streamlit

### Problem: "No recommendations shown"
**Check:**
1. Go to System Status → see "Detectors produced N signals"
2. If N=0, data may not load or thresholds too strict
3. Go to Explore a Source → verify data exists
4. Check Ollama status (sidebar) — if 🔴, narratives may fail

**Fix:**
- Rerun app (press R)
- Check that config/entities.yaml points to correct CSV path
- Verify CSV files exist in data_dir

### Problem: "Recommendation has wrong category"
**Check:**
1. Click recommendation → expand "Evidence"
2. Verify signals that triggered it
3. Go to How It Works → Signal → Category Logic
4. If mapping seems wrong, check config/domains_fdm.yaml

**Fix:** May need to adjust combination rules in domains_fdm.yaml

### Problem: "Ollama not reachable"
**Check:**
1. Sidebar shows 🔴 next to "Local Ollama"
2. Narratives fall back to deterministic template (still works)

**Fix:**
```bash
# Terminal
ollama serve
# Wait for "Listening on" message
# Refresh dashboard (press R or reload browser)
```

### Problem: "Permission denied — can't see other RMs' customers"
**Expected behavior:** Role-based access control
- Supervisor role: sees all accounts
- RM role: sees only their own account_id mappings

Check sidebar → "Signed in: [role]"

---

## Navigation Map: How Sections Connect

```
Overview (start here)
    ↓
How It Works (understand system)
    ↓
Prepare for Client Call (main work)
    ├→ Explore a Source (debug data)
    └→ Evidence links → How It Works (understand signals)
    
Digests (portfolio view)
    └→ Drill into worklist
    
ML Opportunities (future state)
    └→ Understanding ML constraints
    
Onboard a New Schema (add data)
    └→ Follow setup docs
    
Verification Proofs (validate correctness)
System Status (monitor health)
Technique Reference (find docs)
```

---

## Next Steps

1. **Launch dashboard:** `streamlit run dashboard/app.py`
2. **Start with Overview** (5 min intro)
3. **Go to "Prepare for Client Call"** (daily worklist)
4. **Click a customer** to see recommendation details
5. **Use "Explore a Source"** to verify data
6. **Refer to "How It Works"** when confused

---

## Resources

- **Conceptual understanding:** Read `docs/USER_GUIDE_GETTING_STARTED.md` (external docs, in order)
- **Configuration:** Edit `config/entities.yaml`, `config/domains_fdm.yaml`, etc. (5 files)
- **Debugging:** Go to "System Status" in dashboard, then "Explore a Source"
- **Adding data:** Use "Onboard a New Schema" tab, then follow Stage 2 setup docs

---

## Key Philosophy

**The dashboard is a window into a deterministic system.**

- Every recommendation is traceable to CSV data + rules
- No black boxes — click through to see the evidence
- All rules are in YAML (readable, auditable)
- Confidence scores are decomposable (see which signals/domains confirm)

**You should never have to trust the system — you can verify it.**

