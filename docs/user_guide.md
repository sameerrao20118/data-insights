# User Guide

Practical how-to. For what's actually verified working vs. NOT RUN, see
[`current_state.md`](current_state.md). For system design, see
[`architecture.md`](architecture.md).

## Where you are right now

- **Steps 1-6c below (local, offline, Ollama-only): alive and verified.**
  You can run both pipelines (endogenous transaction events and exogenous
  market/political events) end to end today, right now, with zero setup
  beyond what's in Step 1.
- **Step 7 (Snowflake): not yet connected.** This needs your own account
  actions (create a warehouse, load data, set credentials) that only you
  can do — see [`snowflake_setup.md`](snowflake_setup.md) for the full
  walkthrough. Nothing else in the pipeline is blocked on this; it's an
  optional second data source, not a dependency.

If you just want to see the thing work end to end, do Steps 1-5 and stop
there.

## 1. Prerequisites

- Python 3 with a virtualenv at `.venv/` (already set up in this repo)
- [Ollama](https://ollama.com) installed and running locally, with at least
  these two models pulled (used by config/rules.yaml):
  ```bash
  ollama pull qwen2.5:7b     # narrator
  ollama pull llama3.1:8b    # judge (deliberately a different model)
  ```
  Verify it's running: `curl http://127.0.0.1:11434/api/tags` should return
  a model list, not a connection error.
- Nothing else. No Snowflake account, no API keys, no internet access
  needed for the default (`offline_ollama`) profile.

## 2. Setup

```bash
cd /Users/sameera/code/datainsights
python3 -m venv .venv          # if not already created
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Generate (or regenerate) the synthetic dataset

```bash
python data_generator/generate_data.py
```

Writes to `data_generator/output/` — 9 CSVs plus
`protected_evaluator_only/trigger_events.csv` (ground truth, kept separate
on purpose — see `data_generator/output/protected_evaluator_only/README.md`).

To generate an independent second dataset (different seed) for holdout use:
```bash
python data_generator/generate_data.py --seed 1337 --out-dir data_generator/output_holdout
```

You don't need to re-run this unless you want a different dataset — the
committed pipeline already ran against the default (seed 42) dataset.

## 4. Run the full pipeline

```bash
python -m datainsights.cli
```

This reads transactions → runs the detector → applies cooldown → ranks →
generates a narrative for the top 15 detections (via local Ollama, with a
deterministic-template fallback if Ollama is unreachable or its output
fails validation) → persists everything to `var/state.sqlite` → writes a
digest to `var/insights/digest_<run_id>.md`.

**Useful flags:**
```bash
python -m datainsights.cli --as-of 2024-06-30      # evaluate as of a different date
                                                     # (only transactions on/before
                                                     # this date are visible)
python -m datainsights.cli --max-narratives 5       # fewer/more LLM calls
python -m datainsights.cli --no-narratives          # fast, detector+ranking only,
                                                     # skips all Ollama calls
python -m datainsights.cli --profile snowflake_trial_ollama   # NOT RUN yet --
                                                     # will fail closed unless you've
                                                     # set the SNOWFLAKE_POC_* env vars
                                                     # (see section 7)
```

Re-running with the same `--as-of` is safe and idempotent: no duplicate
active detections get created, and narratives already generated are served
from cache (fast — seconds instead of ~1 minute).

## 5. Read the output

```bash
ls var/insights/                      # one digest .md per run
cat var/insights/digest_<run_id>.md   # ranked recommendations with evidence,
                                        # narrative, and caveats
```

Each entry shows: rank, score, the flagged transaction's evidence
(amount, baseline, date), an "observed facts" / "interpretation" /
"suggested action" narrative, and caveats. Every entry says explicitly
this is synthetic POC output, not a real business conclusion.

## 6. Check status, run the judge, run evaluation

```bash
python -m datainsights.status              # last run, active detection count, cache stats
python -m datainsights.judge.run_sample 10 # offline semantic judge on 10 cached narratives
python -m evaluation.evaluate               # dev-diagnostic precision/recall
python -m pytest tests/ -v                  # detector + macro-event test suite (16 cases)
```

**Read the judge and evaluation output critically, not as a pass/fail
badge** — both modules print explicit caveats about what the numbers do
and don't mean (contaminated development session, correlated narrator/
judge models). That's intentional, not boilerplate to skip past.

## 6b. Run the exogenous (market/political event) pipeline

Step 4 above only reacts to a client's own transactions. A second,
parallel pipeline reacts to external market/political/industry events —
rate changes, tenders, sanctions, disasters — matched to clients by
sector/country. See `docs/architecture.md` ("Second pipeline: exogenous
events") for how it's wired.

```bash
python -m external_events.simulate_external_events   # regenerate the simulated event feed
python -m external_events.demo_scenario               # detect -> rank -> narrate -> digest
```

Writes its own digest to `var/insights/digest_external_macro_demo_*.md`.
Same idempotency/caching behavior as Step 4.

## 6c. Build the unified RM worklist

One row per client, tagging every recommendation (from either pipeline)
with a category (`FINANCING_NEED`, `TREASURY_OPPORTUNITY`, `RISK_REVIEW`,
`ADVISORY_ONLY`, `HEDGING_NEED`, `CAPEX_FINANCING`) and the evidence
behind it:

```bash
python -m datainsights.build_worklist
```

Writes `var/insights/worklist_<timestamp>.csv`. This is the file an RM
would actually work from — see `docs/artifacts/output-reference.html`
for real column-by-column examples and what each category means.

**Or run everything above in one shot:** `./run_demo.sh` — tests, both
pipelines, evaluation, and the worklist, in sequence.

## 7. Wiring Snowflake (optional, when you're ready)

This is a 9-step, click-by-click walkthrough — creating the warehouse/
database/schema, running the provided DDL, loading the three CSVs,
setting your 6 credential env vars, and testing the connection — written
against your actual trial account. It's long enough that it lives in its
own file: **[`snowflake_setup.md`](snowflake_setup.md)**.

Short version once that's done:
```bash
python -m datainsights.cli --profile snowflake_trial_ollama
```
Without the env vars set, this profile fails closed with a clear error
naming exactly which variable is missing — it will never silently fall
back to offline data.

## 8. Adjusting detector/ranking parameters

Edit `config/rules.yaml` — thresholds, baseline window, cooldown days,
ranking weights, narrative/judge model names, evaluation match window.
Nothing is hardcoded in the detector code itself. All values there are
marked provisional in `docs/detector_spec_large_incoming_payment.md` — if
you change one, re-run the test suite and the evaluation harness to see
the effect.

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `DataSourceError: Unbounded read... refused` | You're calling `read_entity` without `start_date`/`end_date` on a large table — pass a date range. |
| Narrative source shows `deterministic_template (ollama fallback: ...)` | Ollama wasn't reachable, timed out, or its output failed validation — the reason is in the string. Check `ollama list` / that the app is running. |
| `EnvironmentError: Snowflake connection 'poc' is not configured` | Expected — you haven't set the `SNOWFLAKE_POC_*` env vars. See section 7. |
| Pydantic `ValidationError` on profile load | Someone edited a profile YAML to something the safety validators reject (e.g. a `-cloud` model tag, a non-localhost `base_url`, `paid_llm_calls_allowed: true`) — this is by design, not a bug. |
| `python -m datainsights.cli` reruns don't regenerate narratives | Also by design — narrative cache is keyed by evidence hash + model + prompt version. Delete `var/state.sqlite` to force a clean slate if you really want that. |
