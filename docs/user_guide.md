# User Guide

Practical how-to. For what's actually verified working vs. NOT RUN, see
[`current_state.md`](current_state.md). For system design, see
[`architecture.md`](architecture.md).

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
python -m pytest tests/ -v                  # detector test suite (9 cases)
```

**Read the judge and evaluation output critically, not as a pass/fail
badge** — both modules print explicit caveats about what the numbers do
and don't mean (contaminated development session, correlated narrator/
judge models). That's intentional, not boilerplate to skip past.

## 7. Wiring Snowflake (optional, when you're ready)

1. In the Snowflake UI: create a warehouse (XSMALL, auto-suspend ~60s), a
   database, and a schema for this POC.
2. Load the CSVs from `data_generator/output/` (NOT the
   `protected_evaluator_only/` subfolder) into tables matching
   `config/entities.yaml`'s column names/types.
3. In your own shell profile (never in a repo file), set:
   ```bash
   export SNOWFLAKE_POC_ACCOUNT=...
   export SNOWFLAKE_POC_USER=...
   export SNOWFLAKE_POC_PASSWORD=...
   export SNOWFLAKE_POC_WAREHOUSE=...
   export SNOWFLAKE_POC_DATABASE=...
   export SNOWFLAKE_POC_SCHEMA=...
   ```
4. `python -m datainsights.cli --profile snowflake_trial_ollama`

Without these env vars set, this profile fails closed with a clear error
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
