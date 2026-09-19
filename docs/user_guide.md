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
  ollama pull qwen2.5:7b     # narrator -- the id in config/profiles/<profile>.yaml llm.model
  ollama pull llama3.1:8b    # judge (deliberately a different model)
  ```
  Verify it's running: `curl http://127.0.0.1:11434/api/tags` should return
  a model list, not a connection error.
- Nothing else. No Snowflake account, no API keys, no internet access
  needed for the default (`fdm_local`) profile.

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

## 4. Run the pipeline (one pipeline, any bound schema)

```bash
python -m agents.demo_fdm_scenario                          # whole book, no LLM, ~5 s for 60 clients
python -m agents.demo_fdm_scenario --profile legacy_local   # the SAME pipeline on the legacy schema (606 clients)
python -m agents.demo_fdm_scenario --profile sba_local      # ... and on real SBA entities (400 clients)
python -m agents.demo_multiagent_scenario                   # one client, live local-Ollama narration + Tier-2 investigation
```

R23 retired the original CLI (`datainsights.cli`, `build_worklist`,
`external_events.demo_scenario`); everything now runs through
`agents/orchestrator.py`: every registered detector in every domain per
client, canonical reads through the schema's binding, cross-domain rules,
exogenous confirmation, one assembled recommendation. Batch runs are
deterministic and need no model. Outputs: `var/insights/fdm_rm_worklist.csv`
(the RM's ranked list), `var/insights/fdm_rm_digest.md` (the same rows as
prose), `var/insights/fdm_insights.json` (MIMO-shaped, never sent).

The as-of date is the event's date + 90 days for the demo; the whole
pipeline is point-in-time correct — nothing after as-of is visible to any
detector (`tests/test_set_based_book_evaluation.py`, `tests/test_correlation.py`).

## 5. Read the output

```bash
cat var/insights/fdm_rm_digest.md      # ranked recommendations: why now, hypothesis, sized action, talking point
head -5 var/insights/fdm_rm_worklist.csv
```

Every row carries: category (`config/categories.yaml`), hypothesis and
why-now (`config/domains_fdm.yaml`), a sized offer in the account's own
currency with its `sizing_basis`, indicative revenue (illustrative
planning assumptions from `config/rules.yaml`), the confirming domains,
the evidence reference, and `relationship_manager_id` for entitlement.
§5b below is the RM's reading guide.

## 6. Tests, guards and the evaluator

```bash
python -m pytest tests/ -q -k "not live"     # deterministic suite, no Ollama (~700 tests, ~1 min)
python -m pytest tests/ -q                    # + live tests (local Ollama must be up)
python -m ruff check .                        # the lint gate CI runs
python -m evaluation.evaluate                 # legacy ground-truth evaluator -- no producer since R23; replaced by R18
```

The suite is the product's guard rail: config fields must have readers,
categories/rules/packs must be declared, no physical column name may
leak above the semantic layer, no model id may live outside a profile,
every dashboard page must render.

## 6b. Exogenous events

External events are one declarative registry — `config/event_types.yaml`
— and one feed per schema (`external_events/output_fdm/tender_events.csv`,
generated by `python -m data_generator.fdm.generate_fdm_events`). An
event only reaches a client when that client's OWN data confirms
exposure (`external_events/exposure_qualifier.py`); a confirmed event can
override the hypothesis and size the offer from the event's own value.
`docs/adding_a_new_domain.md` and `external_events/README.md` cover
adding an event type (YAML only).

## 6c. The worklist and who may see it

The worklist is scoped **server-side** from the signed-in principal
(`datainsights/identity.py`, R21): an RM sees only their own
`relationship_manager_id` rows, a supervisor the whole book. The demo
profile signs in a supervisor; to look as one RM without editing config:

```bash
DATAINSIGHTS_ROLE=rm DATAINSIGHTS_RM_IDS=RM001 streamlit run dashboard/app.py
```

The bank's identity provider is a declared, NOT RUN contract
(`identity.provider: idp`).

## 6e. Runs on a clock, and the outcome backtest

```bash
python -m datainsights.runs --profile fdm_local            # one recorded, locked, INCREMENTAL run (var/runs.db)
python -m datainsights.runs --profile fdm_local --full     # ignore the watermark, re-evaluate everyone
python -m datainsights.monitor --profile fdm_local --once  # one clock tick; set monitor.enabled: true and drop --once to loop
python -m datainsights.backtest --profile fdm_local --start 2025-07-15 --end 2025-10-04 --step-days 30
```

An incremental run re-evaluates only clients with a canonical row newer
than the previous run's high-water mark, clients a newly ingested
exogenous event qualifies (`python -m external_events.ingest_notices`),
and clients whose carried recommendation is older than the longest
cooldown; everyone else is carried forward. Two overlapping runs cannot
corrupt state (`monitor.max_concurrent_runs`). The backtest is the
question a credit committee asks: as of T, what did we recommend, and
what did RMs record afterwards?

## 6d. The demo dashboard (recommended for showing this to someone)

A CLI transcript is a poor way to show someone where the data comes from
and what the output looks like. `dashboard/app.py` is a local Streamlit
viewer over the exact same files and modules the CLI steps above use —
no new detection/ranking/narrative logic lives in it.

```bash
streamlit run dashboard/app.py
```

Opens at `http://localhost:8501`. The sidebar is organised by what you
want to DO, not by which phase built it:

| Tab | Use it to |
|---|---|
| **Overview** | See the five pipeline stages in one screen |
| **Explore a source** | **The main RM view.** Pick a data source; browse its actual tables and previews; trace one client through every stage; run the whole book and read the worklist, filtered by **My RM code** |
| **Onboard a source** | Add a new data asset: profile → propose → review → human-accept. The only screen that writes to `config/` |
| **ML opportunities** | Scan a schema for ML-eligible measures, set policy, run champion vs. challenger (see `docs/ml_quickstart.md`) |
| **How it works** | The agent-flow diagram for one client |
| **AWS target architecture** | The intended AgentCore shape (nothing here is deployed), plus the local-vs-Snowflake comparison of what changes when a real warehouse is wired in |
| **Verification proofs** | Run the real proof tests live (second schema, real-data schema, second event type, AI evidence check) |
| **Digests** | The markdown digest output |
| **Technique reference** | Which stage is statistical vs. rule-based vs. LLM, and why |
| **Status** | Last run, detection counts, cache stats |

Data sources / Explore a source / Digests / Status work read-only against
whatever is already in `var/` and `data_generator/` — you don't have to
click "run" first. **But note:** the worklist you see is whatever was
last written to `var/insights/`. After changing config or regenerating
data, re-run `python -m agents.demo_fdm_scenario` or the tab's own run
button, or you'll be reading a stale artifact. This is a demo viewer,
not the RM review/tracking system named as a future gap in
`docs/gap_analysis.md`.

## 7. Wiring Snowflake (optional, when you're ready)

This is a 9-step, click-by-click walkthrough — creating the warehouse/
database/schema, running the provided DDL, loading the three CSVs,
setting your 6 credential env vars, and testing the connection — written
against your actual trial account. It's long enough that it lives in its
own file: **[`snowflake_setup.md`](snowflake_setup.md)**.

Short version once that's done:
```bash
python -m agents.demo_fdm_scenario --profile snowflake_trial_ollama   # NOT RUN: source.backend=snowflake raises until a profile can construct it (R5)
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
| Narration looks unchanged on rerun | Narration is validated against the tool evidence and cached per recommendation in `var/agent_traces.db`; delete it to force a clean slate. |
