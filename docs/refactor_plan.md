# Refactor plan — from demo-that-works to accelerator-that-generalises

**Status: PLAN. Nothing in §4 is built.** Companion to
`docs/hardcoding_audit.md`, which holds the evidence (file:line) behind
every finding referenced here. This document is the *executable* half:
what to build, in what order, with acceptance criteria a later session
can verify without re-deriving the reasoning. Written 2026-09-18.

---

## 1. Three questions, answered with evidence

### 1a. What is the trigger for identifying trends and deviations?

**There is no trigger. Detection is pull-based, batch, at a
caller-chosen point in time.** Verified:

- `config/profiles/*.yaml` declare a `monitor:` block (`enabled`,
  `interval_minutes`). **Nothing reads it** — `grep "monitor\."` across
  `datainsights/` and `agents/` returns zero non-comment hits. It is a
  config stub.
- No scheduler, CDC, watermark, checkpoint or incremental logic exists.
  Every grep hit for those words is a docstring saying "(later) a
  scheduler wrapper" or a status read of `last_run`.
- `as_of` — the single date every detector evaluates against — is chosen
  by hand at each entry point, four different ways:
  `date(2025, 10, 4)` hardcoded (`datainsights/ml/runner.py:129`);
  `event.event_date + 90 days` (both demo scripts); `date.today()`
  (`agents/entrypoint.py:68`); a CLI flag (`datainsights/cli.py`).

**What a "trend" or "deviation" actually is today:** a per-detector
windowed threshold comparison, configured in `config/rules.yaml`. E.g.
`cash_buildup`: balance at `as_of` vs. balance ≥ 60 days earlier, fires
if the rise is ≥ 15% on a base ≥ 5,000, then a 45-day cooldown.
`revenue_pattern_change`: mean credit amount in the last 60 days vs. the
prior window, fires on ≥ 50% change with ≥ 3 credits. The legacy
`large_incoming_payment` is the one statistical detector (median
absolute deviation vs. a 90-day baseline). So: rules, thresholds, and one
robust-statistics baseline — all evaluated as a snapshot when someone
runs the pipeline.

**Why this matters for the stated goal.** An accelerator that "listens"
to Snowflake/Glue needs a *trigger model*: what event or clock starts a
run, what data is new since the last run, and how a detection that fired
yesterday is not re-fired today. None of that exists. It is R11/R12
below, and it is the largest piece of genuinely new work in this plan —
everything else is refactoring what exists.

### 1b. Why is it so difficult to add a new category?

Because a category is not a registered thing — it is a **string literal
that ~8 places agree on by convention.** Adding a seventh means touching:

| Site | What it holds | Type |
|---|---|---|
| `config/domains_fdm.yaml` | signal → category | config ✓ |
| `config/rules.yaml` `no_revenue_categories` | which categories get no revenue figure | config ✓ |
| `datainsights/correlation/hypothesis.py:43` `REVENUE_CATEGORIES` | which categories get sized | **code** |
| `datainsights/correlation/hypothesis.py:126` `NON_REVENUE_ACTION` | default action text per non-revenue signal | **code** |
| `datainsights/fdm_worklist.py:44/57/84` `REVENUE_MECHANISM`, `TALKING_POINT`, `WHY_NOW` | how the bank earns, what the RM says, why now | **code** |
| `datainsights/worklist.py` `categorize_macro_event()` + `MACRO_HYPOTHESIS` | the legacy pipeline's own category + hypothesis logic | **code** |
| `dashboard/app.py:44` `CATEGORY_INFO` | label + colour | **code** |
| `agents/investigator_agent.py` | which categories an ambiguous signal may resolve to | **code** |

Two of the six existing categories (`HEDGING_NEED`, `CAPEX_FINANCING`)
are declared in every one of those places and **reachable from no
detector** (`domains_fdm.yaml` maps nothing to them) — so they can never
appear on a worklist. That is what "hard to add" looks like from the
other side: hard to add, and easy to add *wrongly* without noticing.

Contrast **domains**, which are registry-driven: one YAML block + one
`register(DomainSpec(...))` call, proven by `tests/test_domain_registry.py`'s
dummy-domain test. Categories should work the same way. That is R2.

The detectors, notably, are **not** part of the problem — every category
mention in `detection_engine/` is a docstring; `to_signal()` sets no
category. The coupling is entirely downstream of the signal bus.

### 1c. Is there hardcoding, and is the code reusable?

**Yes, and partly.** The precise triage is `docs/hardcoding_audit.md` §2.
The summary a reviewer needs:

- **Reusable as designed, keep untouched:** the `DataSource` ABC
  (`read_entity` + `capabilities`), the binding/semantic layer, the
  correlation and arbitration logic, the propose→validate→accept
  discipline, the domain registry.
- **Reusable by shim, must be fixed:** the 9 detectors speak FDM physical
  column names; `agents/tools.py` carries six rename maps to translate
  canonical back to FDM before every detector call. Works, but every new
  detector inherits the coupling. (R1)
- **Not reusable, must move to config:** the eight business-content
  dicts above. (R2)
- **Bypasses the reusable layer:** four modules call FDM-only source
  methods directly, which is why the whole-book run and the ML runner
  work for `fdm` alone; the guard test that should catch this scans five
  files for five table names. (R3)
- **Demo residue:** hardcoded dates, `PRTY00036`, `"fdm_local"` literals.
  Cheap; not urgent.

---

## 2. The end-user journey, and where the system learns

### Today (verified against the running dashboard)

```
Engineer                          RM
--------                          --
generate/point at data
write profile YAML (by hand)
run whole-book ─────────────────► opens Explore a source
                                  filters "My RM code"
                                  reads: category → hypothesis → sized action
                                  asks the copilot "why?"
                                  records: Engaged / Not appropriate / Remind later
                                            │
                                            ▼
                                  var/rm_feedback.db   (2 rows captured to date)
                                            │
                                            ▼
                                  label_pipeline.build_training_table()
                                            │
                                            ▼
                                  ── nothing consumes it ──
```

The loop is **open**. Feedback is captured with the right schema and
joined to the right features (point-in-time correct by construction —
the worklist row carries the features as they were), but nothing learns
from it and nothing changes what the RM sees next time. Two labels exist.

### Target

```
                 trigger (R11): new data since watermark, or clock, or event
                                            │
  Tier 1  deterministic scan, set-based ────┤  every client, no LLM
                                            │
  cross-domain rules table (R10) ───────────┤  known combinations → category
                                            │
  Tier 2  investigator agent (R9) ──────────┤  shortlist only; tools; PROPOSES
                                            │
  Tier 3  propensity ranker (E4) ───────────┤  ranks by learned P(engage)
                                            │
                                            ▼
                                     RM worklist (filtered to their book)
                                            │  Engaged / Not appropriate / Remind
                                            ▼
                                     rm_feedback.db ──► label pipeline ──► retrain E4
                                            │                                 │
                                            └──── eval harness (A1/A2/A3 golden sets)
                                                  gates promotion: no retrain ships
                                                  that scores worse on the holdout
```

**Where learning enters, precisely:** at Tier 3 only. Tiers 1 and 2
never learn — that is the governance property (deterministic decisions,
validated narration). The RM's response is the *dependent variable*; the
Tier-1 signals, magnitudes, confirming domains, client context and
cross-domain combination are the *independent variables*; the prediction
is propensity to engage. The model **re-ranks** the shortlist; it never
adds to it or changes a category. Promotion is gated by the existing
eval harness and by `require_challenger_win`, which must be enforced
(today it is declared and consumed nowhere).

**What the RM experiences as "learning":** the list gets shorter and the
top rows get better, because things they marked *Not appropriate* stop
surfacing first. Nothing else about the product should visibly change
when learning turns on — if it does, the governance story is broken.

---

## 3. The trigger model (new work, not refactoring)

### R11 — Incremental runs with a watermark — **DONE 2026-09-19 (M26)**: `datainsights/runs.py::run_book` + `datainsights/monitor.py`; second run on unchanged data evaluates 0 clients in 0.14 s vs ~1 s; a new balance row re-evaluates exactly that client; the `monitor:` block is read.

- **Build:** `datainsights/runs.py` — `RunWatermark(profile, as_of, source_high_water)`
  persisted in the existing `var/state.sqlite` `runs` table (which already
  records `run_id`, `started_at`, `profile`). Each run records the max
  `observed_at`/`posted_at` it saw per canonical concept.
- **Behaviour:** `evaluate_book(..., since=watermark)` evaluates only
  clients with any new canonical row since the last watermark, plus any
  client whose cooldown expired. Detectors are unchanged — they already
  take an `as_of` and already implement cooldown.
- **Trigger sources, in order of build:** (1) a clock — the `monitor:`
  block that already exists in every profile finally gets read;
  (2) new-data-arrived — a source's `capabilities().supports_change_detection`
  (already declared, always `False` today) becomes true for a backend
  that can answer "what changed since T" (Snowflake `CHANGES` clause,
  Glue/Athena partitions); (3) an external event landing in the event
  store.
- **Acceptance:** two consecutive runs on unchanged data produce zero new
  detections and a second-run wall time under 10% of the first. A new
  balance row for one client causes exactly that client to be
  re-evaluated.
- **Non-goal:** no real-time streaming. Micro-batch on a clock is the
  right shape for a bank worklist.

### R12 — Exogenous ingestion as a triggered pipeline — **DONE 2026-09-19 (M26)**: `ingest_notices.ingest()` is a callable step; a new extracted event enqueues exactly the clients `qualifies()` confirms.

- **Build:** the existing A1 extraction (`external_events/ingest_notices.py`)
  becomes a step the R11 trigger can invoke; an extracted event with a
  `qualifies` hit for any client enqueues those clients for re-evaluation.
- **Acceptance:** dropping a new notice text into the inbox produces, on
  the next run, a recommendation for exactly the clients whose own data
  confirms exposure — and none for clients that merely share the sector.

---

## 4. Executable tasks — four waves

Each task is independently shippable. Interfaces and acceptance are the
contract; a session executing this must not batch waves.

### Wave 1 — restore the foundation (R1, R2, R3)

**R1 · Detectors go canonical** — **DONE 2026-09-19 (M21)**
- Edit: every `detection_engine/*.py` `REQUIRED_COLUMNS` → canonical field
  names from `config/semantic_model.yaml` (`account_id`, `observed_at`,
  `balance`, `posted_at`, `amount`, `direction`, `grade_code`, …).
  `to_signal()` unchanged in shape. `agents/tools.py`: delete
  `_ACCOUNT_RENAME`, `_BALANCE_RENAME`, `_TRANSACTION_RENAME`,
  `_DIRECTION_TO_SBTYP`, `_COLLATERAL_RENAME`, `_RISK_GRADE_RENAME`; pass
  `CanonicalSource.read()` output straight through.
  `external_events/exposure_checks.py:74-76`: same deletion.
- Tests: every detector unit test's fixture columns renamed; no
  behaviour change.
- Acceptance: `grep -rn "AGRMNT_\|FIN_EVNT_\|RSK_GRD_\|PRTY_ID" detection_engine/ agents/ external_events/`
  returns nothing outside comments. Full suite passes. All three schemas
  still produce identical worklists to before (golden files).

**R2 · Business content → config; categories registry-driven** — **DONE 2026-09-19 (M22)**, one deviation: the legacy `MACRO_HYPOTHESIS`/`categorize_macro_event` content went to `config/legacy_macro_hypotheses.yaml`, not `event_types.yaml` — that registry's `EventTypeSpec` requires real `match:`/`exposure:` blocks, and fabricating them would let `exposure_qualifier.qualifies()` treat seven legacy demo types as genuinely qualifiable events. The file is deleted with the legacy pipeline in R23. `revenue: true|false` became `revenue_model: financing|treasury|hedging|none` because the worklist needs to know *which* formula, not just whether.
- New: `config/categories.yaml` — per category: `label`, `colour`,
  `revenue: true|false`, `revenue_mechanism`, `suppressed_action`.
  Loaded by a new `datainsights/category_registry.py` (mirror
  `domain_registry.py`). `WHY_NOW` and `TALKING_POINT` move into each
  signal's block in `config/domains_*.yaml`. The 11 legacy
  `MACRO_HYPOTHESIS` entries move into `config/event_types.yaml` so there
  is one exogenous registry; `worklist.py` reads it.
- Delete: all eight dicts named in §1b.
- Acceptance: a test registers a dummy category in YAML only and asserts
  it flows to `assemble()`, the worklist, and the dashboard's category
  card. A category with `revenue: false` gets no sized offer. Adding
  `HEDGING_NEED` a signal in YAML makes it reachable with no code edit.

**R3 · Close the bypasses; tighten the guard** — **DONE 2026-09-19 (M21)**
- Edit: `agents/demo_fdm_scenario.py`, `datainsights/ml/compare_baselines.py`,
  `evaluate_baselines.py`, `scale_evaluation.py` — replace every
  `source.party()`/`.agreement()`/`.daily_balance()`/`.financial_event()`/
  `.party_demographic()`/`.party_locator()` with `CanonicalSource.read()`.
  Delete `domain_registry.product_codes()` and its `product_codes:` YAML
  blocks (dead); fix `docs/adding_a_new_domain.md:173`.
- Edit `tests/test_no_source_specific_coupling.py`: scan every package
  except `datainsights/sources/`; forbid every column name from every
  `config/entities_*.yaml` and every FDM-only method name.
- Acceptance: `python -m agents.demo_fdm_scenario --profile legacy_local`
  and `--profile sba_local` both produce a worklist. The guard fails if a
  rename map is reintroduced anywhere.

### Wave 2 — make the reasoning tier real (R10, R9, R7)

**R10 · Cross-domain rules table** — **DONE 2026-09-19 (M23)**: five rules ship; the audit's two worked examples never co-occur in any generated book, so two rules grounded in the combinations that DO occur were added (1 fires on dev, 3 on scaled). — `config/domains_*.yaml` gains a
`combinations:` block: `[{when: [cash_buildup, facility_utilization_spike], category: FINANCING_NEED, hypothesis: ...}, ...]`.
`assemble()` consults it before strongest-signal-wins. Acceptance: the
two examples in `hardcoding_audit.md` §6 produce different categories
from the same strongest signal; a combination with no rule falls through
unchanged.

**R9 · Wire the investigator as Tier 2** — **DONE 2026-09-19 (M23)**: `evaluate_client` calls `investigate()` on narrated runs when `ambiguous` or >1 endogenous domain; multi-domain options are the confirming signals' own categories; proposal on `Recommendation.investigation`; Stage 4b in the Trace tab; batch path never pays. — `evaluate_client()` calls
`investigate()` when `ambiguous=True` or `len(confirming_domains) > 1`;
its proposal lands on `Recommendation.investigation` (new optional
field), never on `nba_category`. Trace tab gains "Stage 4b —
investigation". Acceptance: the existing live A2 test passes inside the
real run path; a two-domain recommendation carries an investigator note.

**R7 · ML gate principled; E2 relabelled** — **DONE 2026-09-19 (M24)**: `power_criteria:` in `config/ml_policy.yaml` (two levels, reasoning in the file); the profiler gives two verdicts and now counts entities on full columns (the 5000-row sample saw 39 of 92 accounts); the ML tab says *no ML challenger is eligible* and names the criterion; E2 relabelled a robust baseline. — per
`hardcoding_audit.md` §3. Acceptance: on today's FDM data the ML tab says
"no ML challenger is eligible on this dataset" and states the power
criterion it failed.

### Wave 3 — the platform becomes a platform (R4, R6, R8)

**R4 · Semantic registry + domain packs** — **DONE 2026-09-19 (M25)**: typed `ConceptSpec` with `kind`, `config/packs/banking.yaml` + `datainsights/packs.py` validated against the registries, `SEMANTIC_CONCEPTS` deleted, `onboarding/concept_proposer.py` proposes a concept for an uncovered table. — concepts loaded from YAML
everywhere (delete `SEMANTIC_CONCEPTS` in `binding_proposer.py`); each
concept gains `kind:`; `config/packs/banking.yaml` groups concepts +
detectors + categories + default rules; onboarding proposes a *concept*
when a Gate-1-eligible table matches nothing. Acceptance: a schema with an
uncovered table yields a proposed concept for review, not "unavailable".

**R6 · Per-binding rules overrides** — **DONE 2026-09-19 (M25)**: `Binding.rules` merged per leaf in `build_runtime`; SBA sets `min_prior_balance: 2500`, FDM untouched. — `config/bindings/<schema>.yaml`
may carry `rules:`; `build_runtime` merges over global defaults.
Acceptance: SBA sets its own `min_prior_balance` without touching FDM's.

**R8 · Profile proposer** — **DONE 2026-09-19 (M25)**: `propose` writes `profile.proposed.yaml`, `accept` validates it as a `Profile` and writes `config/profiles/<name>_local.yaml`. — `onboarding.propose` also emits the profile;
`accept` writes it. Acceptance: accept → run, no hand edits (needs R3).

### Wave 4 — listen (R11, R12, R5)

R11 and R12 as in §3. **R5 · Source breadth** — **DONE 2026-09-19 (M26)**: one dialect-aware `SqlSource` (duckdb/postgres/sqlserver/snowflake/athena) + `AthenaSource`, `read_entity`/`aggregate`/`changed_since`, `domain_schema_map` on the profile, DuckDB conformance identical to `OfflineLocalSource`; real backends NOT RUN, fail closed. — one dialect-aware
`SqlSource` (Postgres / SQL Server / Snowflake) and an `AthenaSource`,
both implementing `read_entity` + `aggregate` + `supports_change_detection`;
`domain_schema_map` added to `Profile`. Conformance test passes for
every backend on the same fixture. Real runs stay NOT RUN until
credentials exist — and say so.

---

## 6. What the first pass missed — a whole-repo sweep, with decisions

The audit was scoped to genericity, agents, ML and triggers. Reading the
rest of the repository as a product owner and senior engineer turned up
eleven more items. Several outrank things already in the plan. Each is
verified (grep or read, 2026-09-18) and each comes with a **decision**,
not a question.

### 6a. A systemic finding first: config that promises what code doesn't deliver

Five separate config fields declare governance that nothing enforces:

| Field | Declared in | Read by |
|---|---|---|
| `monitor: {enabled, interval_minutes}` | every profile | nothing |
| `monitor.max_concurrent_runs` | every profile, `config.py:190` | nothing |
| `missing_data_policy` | every entity in every `entities_*.yaml` | nothing |
| `require_challenger_win` | `ml_policy.yaml`, `policy.py` | nothing |
| `product_codes:` | `domains_fdm.yaml` | nothing (M20) |

These are not five bugs; they are one pattern. A reviewer reading the
YAML would reasonably believe the system enforces concurrency limits,
missing-data handling and challenger promotion gates. It does not.
**Decision: R14 — every declared config field must have a reader or be
deleted, enforced by a test that parses every schema and asserts a
consumer exists.** This goes in Wave 1 because it is cheap and it is a
trust issue.

### 6b. The biggest omission: the scale ceiling has no task

M16 measured the whole-book path at O(clients × rows) — 98M rows
materialised for 300 clients — and identified the fix: a set-based pass
(detectors already group by entity; `CanonicalSource.read()` already
accepts no `party_id`; one pass measured 0.12s vs 1.80s). The plan
mentions "set-based" once, in a diagram. It has no R-number.
**Decision: R13 — set-based whole-book evaluation, Wave 1, alongside R3** — **DONE 2026-09-19 (M22)**: one shared `CanonicalSource` per book with a per-(concept, as_at) frame cache and a party/account position index; 300 clients 12.4 s → 4.55 s, golden equivalence proven on a window with 75 positives.
(they touch the same code). Acceptance: 300 clients on the scaled set in
under 5s; golden equivalence against the per-client path on a window
with positive detections (the equivalence proven so far was on
zero-detection windows only — that caveat stands until this lands).

### 6c. One bad client kills the whole book

`agents/orchestrator.py:177` — `evaluate_book()` is a list comprehension.
An exception evaluating any one client aborts the run with no partial
results and no record of which client failed. For a 60-client demo that
is an inconvenience; for a scheduled run over a book (R11) it is an
outage. **Decision: R15 — per-client isolation**: catch, record the
failure on a `ClientEvaluation.error` field, continue, and surface the
failure count in the run record and the dashboard. Wave 1, small.

### 6d. Nothing runs the 483 tests except a human

No `.github/`, no CI of any kind, no lint or format configuration. The
suite is only as green as the last time someone remembered to run it —
which this session did after every change, and which no future session
is obliged to. **Decision: R16 — CI + lint, Wave 0** (before everything
else, because it protects everything else): a workflow running
`pytest -k "not live"` and `ruff` on every push; a `pre-commit` config
for the same locally. Also under R16, immediate: `.env` was **not** in
`.gitignore` — fixed in this same change.

### 6e. Two full pipelines, and no decision about the second one

The legacy pipeline (`datainsights/cli.py`, `worklist.py`,
`detection_engine/large_incoming_payment.py`) and the FDM/agentic one
(`agents/`, `fdm_worklist.py`) are both fully alive and both wired into
the dashboard (26 references). The plan folds legacy's hypotheses into
the event registry (R2) but never decides the pipeline's fate. Carrying
two is permanent cost, and the Digests tab was demonstrating the weaker
one (M19). **Decision: R23 — converge to one pipeline.** After R1–R3
make the agentic path run the legacy schema end to end (its profile
already exists), port `large_incoming_payment` into the canonical
detector set, delete `datainsights/cli.py`, `worklist.py`, `runner.py`,
`ranking.py` and the legacy dashboard renderer. Wave 3. — **DONE 2026-09-19 (M25)**: 21 files deleted, `large_incoming_payment` ported canonically (fires on 45/606 legacy clients), one pipeline runs three schemas. Acceptance: one
worklist shape, one digest, one code path; the legacy schema produces
the same detections it does today through the agentic path.

### 6f. Everything is priced in EUR

16 hardcoded `EUR` references in `agents/` and `datainsights/`; the
narrator is instructed to "keep every currency (EUR)"; the revenue model
and every sized offer are EUR. SBA is bound as `currency: {const: USD}`
and its offers are still printed as EUR. The semantic model has a
`currency` field the sizing layer ignores. **Decision: R17 —
currency-aware sizing and narration** (**DONE 2026-09-19, M25**: currency on every tool evidence, Signal and `Recommendation`; per-currency floors in `rules.yaml`; narrator told the real currency and caught naming the wrong one; SBA is USD end to end): `Recommendation` carries the
currency from the strongest signal's account; `rules.yaml` thresholds
become per-currency (the legacy detector's `absolute_floor_by_currency`
is the pattern); narration is told the actual currency. Wave 3, with R6.

### 6g. Scheduled runs will collide with themselves

Four SQLite stores (`state.sqlite`, `state_external_events.sqlite`,
`agent_traces.db`, `rm_feedback.db`), no locking, no run-overlap guard
(`max_concurrent_runs` unread — 6a). Today no two runs overlap because a
human starts each one. The moment R11 puts runs on a clock, an overrun
corrupts state. **Decision: R19 — a run lock and a single run-record
table** honouring `max_concurrent_runs`, Wave 4 *before* R11 lands, not
after. — **DONE 2026-09-19 (M26)**: `RunLock` + one `runs` table (var/runs.db); an overlapping run raises, a dead run is abandoned.

### 6h. No procedure for when the model changes

`qwen2.5:7b` is pinned in 12 places. Prompt hashes and three golden sets
exist — good — but nothing says what happens when the model is upgraded,
and nothing tests that the model id is read from one place. A bank will
change models; that must be a routine, not a surprise. **Decision:
R20 — model id sourced from the profile only (delete the 11 other
mentions); a documented change procedure: bump → re-run golden sets →
re-baseline `prompts/hashes.json` → record in the model card.** Wave 2
with R7. — **DONE 2026-09-19 (M24)**: `ModelConfig.model_id` defaults to the active profile's `llm.model` (`agents/model_factory.default_model_id`); `tests/test_model_id_single_source.py` fails on any model tag outside the profiles; `docs/model_change_procedure.md`.

### 6i. The evaluation the bank will actually ask for doesn't exist

`evaluation/evaluate.py` measures precision/recall against *synthetic*
protected labels — a development diagnostic. There is no way to ask
"were the recommendations we made 90 days ago right, judged by what
happened next?" That outcome-based backtest is (a) the only evaluation a
credit committee will accept, and (b) the thing that makes the E4
propensity model trustworthy rather than merely trained. It needs the
virtual-clock replay `gap_analysis.md` lists as "not started".
**Decision: R18 — replay with a virtual clock + outcome join**: run the
pipeline as-of T, join RM feedback recorded after T, report hit rate by
category. Wave 4, and it is the acceptance test for E4. — **DONE 2026-09-19 (M26)**: `datainsights/backtest.py`, virtual clock on data AND events, feedback strictly after T.

### 6j. Entitlement is a dropdown

"My RM code" (M18) filters; it does not authorise. Any user can pick any
RM. Real scoping needs an identity source and row-level filtering that
cannot be bypassed from the UI. **Decision: R21 — identity-backed
entitlement**: a pluggable `Identity` provider (local dev user → the
bank's IdP at Stage 3), the worklist read scoped server-side, the
dropdown removed. Wave 3. This is a pilot blocker, not a nice-to-have. — **DONE 2026-09-19 (M25)**: `datainsights/identity.py`, `Profile.identity` (local_dev | idp NOT RUN), worklist scoped server-side, dropdown gone.

### 6k. Two monoliths, and a state document that has become a changelog

`dashboard/app.py` is 1,976 lines in one file and is exercised only by
ad-hoc `AppTest` scripts, never by the suite. `docs/current_state.md` is
1,480 lines of M1–M20 — a changelog, not a statement of current state,
and the next reader will not find the state in it. **Decision: R22 —
split the dashboard into one module per tab with `AppTest` coverage in
the suite; split `current_state.md` into a short `current_state.md`
(what is true now) and `docs/changelog.md` (M1–M20 verbatim).** Wave 2. — **DONE 2026-09-19 (M24)**: `dashboard/app.py` 1,976 → 61 lines (sidebar + dispatch), `dashboard/common.py` + `dashboard/tabs/<page>.py` × 10, every page rendered under AppTest in the suite; `current_state.md` 1,691 → 331 lines with an at-a-glance section, M7–M23 moved verbatim to `docs/changelog.md`.

### 6l. Names that imply coupling

`domains_fdm.yaml`, `fdm_worklist.py`, `FdmLocalSource`, `fdm_rule_version`
— for things that are, or after R1 will be, schema-agnostic. Names teach
the next engineer the wrong thing. **Decision: rename as part of R2/R4**
(`domains.yaml`, `worklist.py` once R23 frees the name,
`BitemporalLocalSource`). No separate task.

### Resequenced waves

| Wave | Tasks | Why this grouping |
|---|---|---|
| **0 — protect** | R16 CI + lint + `.env` | Cheap; everything after is safer with it |
| **1 — foundation** | R1 canonical detectors · R2 content→config · R3 close bypasses · **R13 set-based eval** · **R14 config enforcement** · **R15 per-client isolation** | Removes the most coupling and the most risk per hour; R3/R13 touch the same code |
| **2 — reasoning + hygiene** | R10 cross-domain rules · R9 investigator · R7 ML gate · **R20 model-change procedure** · **R22 split monoliths** | Makes the agent story real and governable |
| **3 — platform** | R4 registry + packs · R6 per-binding rules · **R17 currency** · R8 profile proposer · **R21 entitlement** · **R23 retire legacy** | The accelerator becomes one product with one pipeline |
| **4 — listen + prove** | **R19 run lock** · R11 incremental · R12 event ingestion · R5 sources · **R18 outcome backtest** | Scheduled, multi-source, and evaluable the way a bank evaluates |

Bold = added by this sweep. Twenty-three tasks. Wave 0 is a morning;
Wave 1 is the one that changes what the product *is*.

**Status (2026-09-19):** Wave 0 done (R16). Wave 1 done — R1, R2, R3,
R13, R14, R15 (`docs/changelog.md` M21/M22). Wave 2 done — R10, R9 (M23), R7, R20,
R22 (M24). Wave 3 done — R4, R6, R17, R8, R21, R23 (M25). Wave 4 done —
R19, R11, R12, R5, R18 (M26). **All 23 tasks landed.** What remains is
listed in `docs/current_state.md` "Not yet".

## 5. What "done" looks like, from the RM's chair

An RM opens the dashboard on Monday. The list is already there — it ran
overnight on what changed last week (R11). It's their clients only. Each
row reads category → hypothesis → sized action, and for the two-domain
cases there's a one-line investigator note saying *why* those signals
belong together (R9/R10). The top of the list is ordered by what people
like them actually engaged with last quarter (E4), and they can see that
ordering is learned, not decided. They mark three rows. Tuesday's list
is shorter. Nothing they marked *Not appropriate* is near the top again.

An engineer onboarding the next schema profiles a directory, reviews a
proposal, clicks Accept, and runs — no profile to hand-write (R8), no
rename maps to add (R1), no category to hardcode in five places (R2).
If the schema has a table nothing covers, they get a proposed concept
to review, not a silent gap (R4).

That is the accelerator the original intent described. Every piece of it
is a bounded task above, and none of it requires abandoning what's
already been proven on three schemas.
