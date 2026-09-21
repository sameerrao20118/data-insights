# User Guide

Practical how-to. For what's actually verified working vs. NOT RUN, see
[`current_state.md`](current_state.md). For system design, see
[`architecture.md`](architecture.md).

## Where you are right now

- **Steps 1-6e below (local, offline, Ollama-only): alive and verified.**
  There is **one** pipeline (R23 retired the original CLI): it reads a
  client's own data *and* correlates external events, and it runs the
  FDM, legacy and SBA schemas unchanged — only the profile differs. You
  can run all of it end to end today with zero setup beyond Step 1, and
  the whole-book batch needs no model at all.
- **Step 7 (Snowflake): not yet connected.** This needs your own account
  actions (create a warehouse, load data, set credentials) that only you
  can do — see [`snowflake_setup.md`](snowflake_setup.md) for the full
  walkthrough. Nothing else in the pipeline is blocked on this; it's an
  optional second data source, not a dependency.

If you just want to see the thing work end to end, do Steps 1-5 and stop
there.

## 1. Prerequisites

- Python 3 with a virtualenv at `.venv/` (already set up in this repo)
- [Ollama](https://ollama.com) installed and running locally, with the
  model your profile names pulled:
  ```bash
  ollama pull qwen2.5:7b     # whatever `llm.model` says in config/profiles/<profile>.yaml
  ```
  That profile field is the ONE place a model id lives (R20); to change
  it, follow [`model_change_procedure.md`](model_change_procedure.md).
  Ollama is needed only for narration, the investigator, the RM copilot,
  event extraction and schema onboarding — every batch run, the worklist,
  the incremental runs and the backtest are deterministic and run with
  Ollama switched off.
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

## 3. Generate (or regenerate) the synthetic datasets

Three schemas, all read by the same pipeline. The FDM one is the default:

```bash
# FDM -- the primary synthetic book (60 clients, bi-temporal) + its event feed
python -m data_generator.fdm.generate_fdm --seed 42
python -m data_generator.fdm.generate_fdm_events

# legacy schema -- structurally different (flat, different table names), 606 clients
python -m data_generator.generate_data

# SBA -- REAL U.S. SBA PPP entities/sectors/loans, synthetic deposits on top (one-off download)
python -m data_generator.external.fetch_sba && python -m data_generator.fdm.load_sba
```

Each writes its own `protected_evaluator_only/` directory (ground truth,
kept separate on purpose — see that directory's README). Nothing in the
pipeline can read it: the contract doesn't define it, the source refuses
a path containing it, and a test fails the build if live code so much as
references it.

Add `--seed 1337` (FDM) or `--out-dir data_generator/output_holdout`
(legacy) for an independent holdout set. You don't need to re-run any of
this unless you want different data.

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

### 5b. What a Relationship Manager actually gets

One row per client, ranked so the first rows are the ones most worth the
next hour: revenue-earning categories first, then by indicative revenue,
then by signal strength. Risk and advisory rows sort last — they matter,
but they are not what the list is opened for. A real row from
`var/insights/fdm_rm_worklist.csv`, read top to bottom the way an RM
would:

| Column | Example | What the RM does with it |
|---|---|---|
| `rank`, `prty_id`, `relationship_manager_id` | 1, PRTY00036, RM007 | Where to start, and whose client it is (the list is already scoped to you) |
| `segment`, `sector`, `country` | SME, Manufacturing, ES | Who you are about to call |
| `nba_category` | FINANCING_NEED | The kind of conversation — one of the categories in `config/categories.yaml` |
| `why_now` | "Incoming payment pattern has structurally shifted — financing need changes with it." | The one line that answers *why this client, this week* |
| `hypothesis` | "Winning a public tender creates a cash-flow gap between delivery and payment, confirmed here by the client's own recent revenue growth…" | The reasoning, one level above the number. Say this before quoting a figure |
| `recommended_action` | "Offer working-capital financing of ~EUR 800,000 (25% of the EUR 3,200,000 tender value, illustrative)…" | The concrete, sized ask |
| `indicative_offer_eur` / `indicative_revenue_eur` / `currency` | 800,000 / 21,200 / EUR | The offer and what it would earn the bank — **illustrative planning assumptions**, never a quote. Amounts are in the client's own account currency |
| `sizing_basis` | `25pct_of_event_value_illustrative` | Exactly how that number was derived. If it reads `not_sized_*`, no honest figure could be grounded — say so rather than inventing one |
| `talking_point` | "We noticed your incoming payment pattern has shifted recently…" | An opener you can use verbatim |
| `signal_strength` / `confirming_domains` | 3, `deposits,exogenous` | How much evidence stands behind it, and from where. More domains agreeing = higher confidence, decomposed rather than one opaque score |
| `endogenous_signal_type` / `exogenous_event_type` | `revenue_pattern_change` / `public_tender_award` | What fired: the client's own behaviour, an external event, or both |
| `evidence_ref` | `EVENT_FINANCIAL:AGR000060:eff=2025-08-21` | The exact record behind the claim — this is what makes a challenge answerable |
| `ambiguous` | False | True means the category is a disclosed simplification; the Trace tab's Stage 4b shows the investigator's second opinion |
| `response_actions` / `recommendation_id` | Customer Engaged / Not Appropriate / Remind Me Later | What you record afterwards. The id is stable, so your response is what the outcome backtest (§6e) later judges the recommendation by |

Two habits worth keeping: **read `why_now` and `hypothesis` before the
number** — the figure is a planning assumption, the reasoning is the
part you can defend; and **record a response even when the answer is
no**. "Not Appropriate" is the single most valuable signal this system
can be given — it is the only thing that will ever tell it which of its
recommendations were wrong.

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
| **Explore a source** | **The main RM view.** Pick a data source; browse its actual tables and previews; trace one client through every stage (including Stage 4b, the investigator's proposal); run the whole book and read the worklist — already scoped to whoever is signed in (R21), not filtered by a dropdown |
| **Discover signals** | Find signals in a book that nobody wrote a rule for: enumerate → screen for novelty → name (local Ollama) → human-accept into **shadow**. A shadow signal is visible but structurally cannot reach an RM worklist until a human promotes it (`docs/signal_discovery_design.md`) |
| **Onboard a source** | Add a new data asset: profile → propose → review → human-accept. The only screen that writes to `config/` |
| **ML opportunities** | Scan a schema for ML-eligible measures, set policy, run champion vs. challenger (see `docs/ml_quickstart.md`) |
| **How it works** | The agent-flow diagram for one client |
| **AWS target architecture** | The intended AgentCore shape (nothing here is deployed), plus the local-vs-Snowflake comparison of what changes when a real warehouse is wired in |
| **Verification proofs** | Run the real proof tests live (second schema, real-data schema, second event type, AI evidence check) |
| **Digests** | The markdown digest output |
| **Technique reference** | Which stage is statistical vs. rule-based vs. LLM, and why |
| **Status** | The narration run record from `var/agent_traces.db` — traces, recommendations narrated, template fallbacks, and which model produced them |

Explore a source / Digests / Status work read-only against whatever is
already in `var/` and `data_generator/` — you don't have to click "run"
first. The sidebar shows who you are signed in as; to look at the book as
one relationship manager, launch with
`DATAINSIGHTS_ROLE=rm DATAINSIGHTS_RM_IDS=RM001 streamlit run dashboard/app.py`. **But note:** the worklist you see is whatever was
last written to `var/insights/`. After changing config or regenerating
data, re-run `python -m agents.demo_fdm_scenario` or the tab's own run
button, or you'll be reading a stale artifact. This is a demo viewer,
not the RM review/tracking system named as a future gap in
`docs/gap_analysis.md`.

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

## 6f. Onboarding your own schema (writing a binding)

None of the 9 detectors, the worklist, or the agent tools ever read a
physical column name. They read through `CanonicalSource`, which reads
through **a binding** — `config/bindings/<name>.yaml`. A binding is the
only file that knows your table is called `EVENT_FINANCIAL` or
`transactions`, and that its posting-date column is spelled
`FIN_EVNT_PSTD_DT` or `booking_date`. Onboarding a new bank's schema
means writing one binding file — never touching a detector, a tool, or
`config/semantic_model.yaml` itself, unless your data has a concept
genuinely nothing there covers yet.

```
config/semantic_model.yaml    canonical vocabulary — Party, Account, Transaction, ...
        ↑ a binding maps physical → canonical
config/bindings/<name>.yaml   ONE FILE PER SCHEMA — this is what you write
        ↑ reads
your physical data            CSVs, Snowflake tables, whatever
```

**Never edit `config/semantic_model.yaml` to onboard a schema.** It's
the shared contract every binding maps onto (`Party`, `Account`,
`Transaction`, `BalanceObservation`, `RiskGradeVersion`,
`PartyMetricVersion`, `CollateralValuation` today, each with typed
fields and a `required: true/false` flag) — read that file first to see
exactly which canonical fields exist and which are mandatory.

### Two real bindings, side by side

`config/bindings/fdm.yaml` and `config/bindings/legacy.yaml` map two
structurally different physical schemas onto the same canonical
`Transaction` concept:

| canonical field | FDM (`EVENT_FINANCIAL` table) | legacy (`transactions` table) |
|---|---|---|
| `transaction_id` | `EVNT_ID` | `transaction_id` (already matches) |
| `account_id` | `AGRMNT_ID_TRN_ACCT` | `account_id` |
| `posted_at` | `FIN_EVNT_PSTD_DT` | `booking_date` |
| `amount` | `FIN_EVNT_AMT` | `amount` |
| `direction` | **derived**: `FIN_EVNT_SBTYP_CD` mapped `{CRD: credit, DBT: debit}` | plain rename of `direction` (the CSV already spells out `"credit"`/`"debit"`) |

Same canonical shape, two different physical realities — one needs a
value translation, the other doesn't. That's the entire point of a
binding.

### The five things a binding can say, per concept

Each concept block under `concepts:` in a binding file
(`datainsights/semantic/binding.py`'s `ConceptBinding`) can use any of:

| Key | Meaning | Worked example |
|---|---|---|
| `entity:` | the physical table this concept reads from | `fdm.yaml`: `Transaction.entity: EVENT_FINANCIAL` |
| `fields:` | `canonical_name: PHYSICAL_COLUMN`, a plain rename | `legacy.yaml`: `account_id: account_id`, `posted_at: booking_date` |
| `derived:` | a field needing a transform: `from:` + `map: {physical_value: canonical_value}` (+ optional `default:`), or a fixed `const:` for a value that isn't a column at all | `legacy.yaml` `Account.derived.currency: {const: EUR}` — these CSVs carry no currency column; it's always EUR in this book |
| `joins:` | when a canonical field lives on a *different* physical table than `entity:`, joined on a key | `fdm.yaml` `Party.joins`: `PARTY_DEMOGRAPHIC` joined on `PRTY_ID` supplies `sector_code`/`sector_name` |
| `bitemporal:` | names the two physical columns holding `valid_from`/`valid_to`, only if the table actually versions rows | `fdm.yaml` `Party.bitemporal: {valid_from: EFFECTIVE_START_DT, valid_to: EFFECTIVE_END_DT}` |
| `unavailable:` | this concept genuinely doesn't exist under this schema — a required, honest reason string, never a silent drop | `legacy.yaml` `CollateralValuation.unavailable: "No collateral entity exists in config/entities.yaml or the legacy generator's output at all."` |

`unavailable:` is not a workaround to avoid mapping something hard — it
is the correct answer when the physical schema has no equivalent data
at all. Downstream code is required to treat it as
`not_available_under_this_binding` and degrade gracefully, never crash
(see the "Verification proofs" dashboard tab, run 1). Do **not** invent
a plausible-looking mapping just to make a concept appear populated —
`legacy.yaml`'s own header comments (lines 8–22) show the expected
level of honesty: they name exactly which fields are missing and why,
distinguishing "genuinely no data" from "data exists in the CSV but
isn't contracted as an entity yet" (real, scoped follow-up work) from
"no such concept in this business at all."

### Step by step: mapping your own schema

1. **Get your column names and a few sample rows** for whatever tables
   cover Party/client, Account, Transaction, and balances. (If any of
   that data is mixed in with label-generation logic covered by this
   project's `protected_evaluator_only/` boundary, ask for a sanitized
   extraction — never read that path directly.)
2. **Copy the closest existing binding as a starting point**
   (`config/bindings/legacy.yaml` if your schema is flat/current-state;
   `config/bindings/fdm.yaml` if it's bi-temporal with a Party/Account
   split).
3. **For every canonical field in `config/semantic_model.yaml`**, decide
   which of the five keys above it needs — a plain `fields:` rename
   covers most cases; reach for `derived:`, `joins:`, or `unavailable:`
   only when the plain rename doesn't fit.
4. **Give the binding a `schema:` name and `contract_ref:`** pointing at
   this schema's entity contract (see `config/entities.yaml` or
   `config/entities_fdm.yaml` for the format — a new schema needs its
   own contract file the same way).
5. **Prove it** the same way the dashboard's "Verification proofs" tab
   does — `tests/test_legacy_binding_end_to_end.py` and
   `tests/test_sba_binding_end_to_end.py` are the templates for the
   end-to-end test a new binding needs (load the binding, read each
   concept, assert the detectors that should work do, and the ones
   correctly reporting `unavailable` don't crash).

### The automated shortcut

For a first draft, a local Ollama model can propose the mapping for
you from profiled column names and samples — it never invents an
entity or column that doesn't exist in your data, and nothing it
proposes becomes real configuration until you review and accept it:

```bash
python -m onboarding.propose <your_data_dir> --name <schema>   # writes onboarding/proposals/<schema>/review.md
# read the review — every proposed mapping, its confidence, and its evidence
python -m onboarding.accept <schema>                            # writes config/bindings/<schema>.yaml + the entity contract + a runnable profile
```

Treat the proposal as a draft, not an answer — the acceptance step is a
human gate, deliberately not bypassable, because a binding becomes live
pipeline configuration the moment it's accepted.

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

## 8. Changing behaviour — which file, for what

Almost nothing about this system's behaviour lives in Python any more.

| You want to change | Edit |
|---|---|
| A detector threshold, window, cooldown | `config/rules.yaml` (per currency where money is involved) |
| …but only for ONE schema | that schema's `rules:` block in `config/bindings/<schema>.yaml` — merged over the defaults, per leaf (R6) |
| What a signal means: category, hypothesis, "why now", non-revenue action | `config/domains_fdm.yaml` |
| Two signals together meaning something new | the `combinations:` table in the same file (R10) |
| Add or retune an NBA category, its revenue model, mechanism, talking point | `config/categories.yaml` (R2) — no Python at all |
| The illustrative revenue/sizing assumptions | `fdm_revenue_model` / `fdm_endogenous_sizing` in `config/rules.yaml` |
| A new external event type, how it matches and sizes | `config/event_types.yaml` |
| Which fields ML may challenge, and the power criteria | `config/ml_policy.yaml` (R7) |
| The model, the source backend, who is signed in, the run clock | `config/profiles/<profile>.yaml` |

Every value in `config/rules.yaml` is **provisional** — structurally
sane, never validated against a real outcome. Change one and re-run
`python -m pytest tests/ -q -k "not live"`; a config field that nothing
reads, or a category/rule/signal that nothing declares, fails the suite
rather than failing quietly at an RM's desk.

Onboarding a whole new schema (a different bank's data model) means
writing one `config/bindings/<name>.yaml` file, never touching a
detector — see **§6f above** for the worked example and the automated
`onboarding.propose`/`onboarding.accept` shortcut.
`docs/adding_a_new_domain.md` covers a different kind of change: adding
a whole new business domain (Risk, Treasury), a category, or a
cross-domain rule.

## 9. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `DataSourceError: Unbounded read... refused` | You're calling `read_entity` without `start_date`/`end_date` on a large table — pass a date range. |
| Narrative source shows `deterministic_template (ollama fallback: ...)` | Ollama wasn't reachable, timed out, or its output failed validation — the reason is in the string. Check `ollama list` / that the app is running. |
| `EnvironmentError: SQL connection 'poc' (snowflake) is not configured -- ... NOT RUN by design` | Expected. No SQL backend has ever run here; it fails closed rather than falling back to local data. See section 7. |
| `RunOverlapError: 1 run(s) already running` | Two runs of the same profile overlapped. That is the run lock (R19) protecting shared state — wait, or raise `monitor.max_concurrent_runs` deliberately. |
| The dashboard says "Nothing in your entitlement on this book" | You are signed in as an RM with no matching rows. Set `DATAINSIGHTS_RM_IDS`, or use the supervisor identity the demo profile ships with (R21). |
| An incremental run evaluates 0 clients | Correct behaviour on unchanged data (R11) — the worklist is carried forward. Use `python -m datainsights.runs --full` to force a re-evaluation of everyone. |
| Pydantic `ValidationError` on profile load | Someone edited a profile YAML to something the safety validators reject (e.g. a `-cloud` model tag, a non-localhost `base_url`, `paid_llm_calls_allowed: true`) — this is by design, not a bug. |
| Narration looks unchanged on rerun | Narration is validated against the tool evidence and cached per recommendation in `var/agent_traces.db`; delete it to force a clean slate. |
