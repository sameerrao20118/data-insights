# ML opportunities — a baby-steps guide

This is the "how do I actually use this" companion to
`docs/ml_strategy_plan.md` (the design/decisions doc). If you just want
to click buttons and read results, start here. If you want the
reasoning behind why it's built this way, read the plan doc.

**The one-sentence version**: this platform never lets a machine-learning
model decide what an RM sees — ML only ever *sharpens a baseline* (what
counts as "normal" for one client), and every step below is either fully
automatic (no config needed) or requires you to explicitly click Save
before anything changes.

---

## Path A — the dashboard (no command line)

```bash
streamlit run dashboard/app.py
```

Open the **ML opportunities** tab in the sidebar. It's three steps, top
to bottom on the page:

### Step 1 — Scan for eligible measures

Pick a schema, click **Scan**. Every numeric column gets **two** verdicts
(R7, `config/ml_policy.yaml` → `power_criteria`, reasoning in the file):

- **Robust baseline** — enough per-entity history (≥ 8 obs/entity, ≥ 30
  entities, a time column, not >20% null, not constant) to compare the
  deterministic baseline against SLOT E2's outlier-robust statistic.
- **ML challenger** — a population (≥ 200 entities), history longer than
  the window (≥ 60 obs/entity) and an evaluation protocol (≥ 100 RM
  outcome labels in `var/rm_feedback.db`).

On every dataset shipped today the banner reads **"No ML challenger is
eligible on this dataset yet"** and names the criterion that failed
(92 entities < 200; 0 labels < 100). That is the correct answer, not a
missing feature: deterministic and robust baselines are the right tool
until the data — and the labels — exist. Every rejection names its
criterion, so you know exactly what the dataset would need.

### Step 2 — Decide

For each measure the platform already knows how to compare
(`BalanceObservation.balance` today — see "what's wired" below), you see:
- Whether it's currently enabled, and **why** (`chosen by: policy` means
  a human set it explicitly; `chosen by: gate1+binding` means the
  platform enabled it automatically because it's both structurally
  eligible and already a canonical measure the schema's binding maps).
- A checkbox to turn it on/off yourself.
- A dropdown to pick the algorithm (`deterministic` = the safe default,
  `isolation_forest` = the sklearn challenger).

**Nothing changes until you click 💾 Save policy.** That writes to
`config/ml_policy.yaml` — the same file a future config-managed
deployment would version-control.

### Step 3 — Run deterministic vs. challenger

Click **▶ Run champion vs challenger**. You'll see, for the whole book:
how many clients the two approaches agree on, how many they disagree
on, and how long it took.

**Read the disagreement number as "what changed," never as "which one
is better."** Without real RM-feedback outcome labels there is no way
to say the challenger is right when they disagree — that's not a gap in
this tool, it's a fact about unlabelled data. The page repeats this
warning every time, on purpose.

### Model registry

At the bottom of the tab: every trained model this platform has ever
registered, each with its **model card** (purpose, data window,
features, metrics, limitations, owner) — expand one to see it. Empty
today, honestly, because no model has been trained yet (see "what's not
built yet" below).

---

## Path B — the command line

Same three steps, scriptable:

```bash
# Step 1: scan a schema
python3 -c "
from onboarding.profiler import profile_directory
from onboarding.ml_profiler import assess
profiles = profile_directory('data_generator/output_fdm/kernel')
for table, cols in assess('data_generator/output_fdm/kernel', profiles).items():
    for c in cols:
        print(f'{table}.{c.column}: eligible={c.eligible} reasons={c.reasons}')
"

# Step 2: decide -- hand-edit config/ml_policy.yaml (see the file's own
# header comment for the precedence rule), or use the dashboard's Save button

# Step 3: run the comparison
python -m datainsights.ml.runner --profile fdm_local
# -> prints a report and writes var/ml_runs/ml_runner_<run_id>.json
```

To propose a NEW measure an LLM should look at (Gate 3, for a schema-specific
field the canonical model has never seen — e.g. `covenant_headroom_pct`):

```bash
python3 -c "
from onboarding.profiler import profile_directory
from onboarding.ml_profiler import assess
from onboarding.ml_measure_proposer import propose_ml_measures
from agents.model_factory import ModelConfig, get_model

data_dir = 'data_generator/output_fdm/kernel'
profiles = profile_directory(data_dir)
eligibility = assess(data_dir, profiles)
model = get_model(ModelConfig(mode='local'))   # model id comes from the active profile (R20)
for p in propose_ml_measures(eligibility, profiles, model):
    print(p)
"
```
Every proposal is either `accepted` (with a proposed name, business
meaning, and confidence) or rejected with a stated reason — **it is
never written to config automatically.** A human decides what to do
with an accepted proposal, the same way `onboarding/accept.py` is the
only thing that writes a proposed schema binding.

---

## What's actually wired today (be honest with yourself about this)

| Piece | Status |
|---|---|
| Scanning ANY schema for eligible measures (Step 1) | Works for all 3 shipped schemas (fdm/legacy/sba) |
| Policy toggles + Save (Step 2) | Works for any schema |
| Running a comparison (Step 3) | **`fdm` schema only.** `datainsights/ml/runner.py`'s `MEASURE_COMPARISONS` maps `BalanceObservation.balance` → `cash_buildup` and `Transaction.amount` → `revenue_pattern_change`, both of which call FDM-specific `DataSource` methods. Legacy/SBA will show "not wired" rather than crash or fake a result. |
| Gate 3 (LLM measure proposer) | Works standalone; not yet wired into the dashboard UI (CLI only, see above) |
| A trained, registered model | **None yet.** SLOT E4 propensity stays unimplemented — it needs real RM-feedback label volume, which is still blocked on access (see `docs/gap_analysis.md`'s D6 row). This is not a bug; it's the correct state until that's true. |
| SageMaker / Snowpark training | Contract-only, both raise `NotImplementedError` naming the exact blocker — nothing runs, nothing is authorized (`datainsights/ml/backends/`) |

---

## Where to look next

- **Design and the "why"**: `docs/ml_strategy_plan.md`
- **Current build status, verified**: `docs/changelog.md` (the M17 section)
- **What's still genuinely missing**: `docs/gap_analysis.md`
- **The condensed, portable summary** (for pasting into a restricted environment): `docs/PROJECT_CONTEXT.md`
