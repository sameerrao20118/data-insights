# Signal Discovery: Design and Current State

**Status: stages 1-8 built and run. Nothing is promoted to production.**

This addresses a gap identified in review: every signal in
`config/domains_fdm.yaml` was hand-authored by someone who already knew
the banking domain. The system could execute signals a person had named,
but had no mechanism for a *new* signal to emerge from patterns in the
data. This document describes the mechanism that now exists, and is
explicit about what it can and cannot establish.

---

## What was already there (for contrast)

The repo already proposes things. It is worth being precise about what,
because the difference is the whole point:

| Layer | Proposer | Proposes |
|---|---|---|
| Which physical columns map to canonical fields | `onboarding/binding_proposer.py` | "CSV's `client_id` probably means canonical `party_id`" |
| Which numeric columns are worth baselining | `onboarding/ml_measure_proposer.py` | "This column looks like a business magnitude, track it" |
| **Which patterns constitute a signal** | **was: nothing** | — |
| **Which signal implies which NBA category** | **was: nothing** | — |

The first two propose *plumbing*. Neither proposes a *rule*.

---

## The foundation: five archetypes

`detection_engine/specs/archetypes.py`

Every one of the nine hand-written detectors is one of five computational
shapes. This is verified, not asserted — `onboarding/reference_specs.py`
expresses all nine as specs, and
`tests/test_signal_specs.py::test_every_handwritten_detector_maps_to_an_archetype`
fails the build if one cannot be:

| Archetype | Hand-written detectors it covers |
|---|---|
| `own_history_deviation` | large_incoming_payment, cash_buildup, revenue_pattern_change |
| `ratio_threshold` | facility_utilization_spike, collateral_coverage_drop |
| `date_proximity` | facility_maturity_approaching, fixed_rate_expiry |
| `absence` | dormancy |
| `ordinal_migration` | rating_downgrade, pd_migration |

A spec names an archetype, the **canonical** fields it reads, and its
parameters. It never names a physical column, so a spec found on one
schema is portable to any schema whose binding supplies those fields —
the same guarantee `config/bindings/*.yaml` already gives detectors.

Two properties were designed in deliberately:

- **`raw_measure` contract.** `correlation/hypothesis.py::_size_endogenous()`
  reads specific keys (`drawn_amount`, `orig_limit`, …) to size an offer.
  Each archetype declares which keys it populates, so a spec-defined
  signal feeding a revenue category is a decision, not an accident.
- **No Python UDF anywhere.** Each shape is a window function, arithmetic
  filter, date diff, anti-join, or lag. That is what makes a future
  SQL/Spark pushdown possible. See "Multi-backend" below.

### Additive, by choice

`detection_engine/*.py` is **untouched**. The nine detectors still run,
still registered, still the production path. Reference specs exist only
as a novelty baseline and as archetype-coverage evidence — never as
detectors, enforced by
`test_reference_specs_are_not_registered_as_production_detectors`.

---

## The pipeline: stages A–E

```
A  enumerate      deterministic   onboarding/signal_enumerator.py
B  screen         deterministic   onboarding/signal_screener.py
C  propose        local Ollama    onboarding/signal_proposer.py
D  accept         human           onboarding/signal_accept.py
E  shadow         structural      config/domains_discovered.yaml
```

### Stage A — enumerate (no LLM)

For each canonical concept the binding supplies, instantiate each
archetype over every compatible field across a small parameter grid.
Gate-1 pruning reuses `ml_profiler.py`'s reasoning: below 20 entities or
8 observations per entity, any pattern is noise.

Bounded by the canonical model, so it cannot blow up combinatorially the
way free-form pattern search would.

### Stage B — screen (no LLM)

**This is where "insights present in the data" actually enter.** Four
deterministic tests:

1. **Fires** on 1–40% of the book. Nobody = useless; most = a threshold.
2. **Stable** — fire rate does not swing >25% across as-of dates.
3. **Novel** — <60% Jaccard overlap with every existing signal's flagged
   client set. *This is the real test.*
4. **As-of safe** — no signal dated after the replay date.

Also computes **co-occurrence lift** between passing candidates, which
yields candidate *combination rules* — the same intuition as the
hand-written `combinations:` block.

### Stage C — propose (local Ollama)

Structural twin of `ml_measure_proposer.py`: one small isolated call per
surviving candidate, validate-or-reject, anything unverifiable dropped
and logged. The model is asked to *interpret* evidence, never to find it
— a candidate that failed Stage B never reaches it.

The one hard validation: **category must be registered in
`config/categories.yaml`**. A model inventing a seventh category is
rejected, never coerced to `ADVISORY_ONLY` — coercion would hide the
failure.

### Stage D — accept (human)

Writes to `config/domains_discovered.yaml` and
`detection_engine/specs/discovered/*.yaml`. Requires `accepted_by`; a
discovered signal records who let it in.

**`config/domains_fdm.yaml` is never written by tooling** — enforced by
`test_tooling_never_writes_to_the_handwritten_domains_file`. A reviewer
can diff machine proposals against human ones and revert one without
disturbing the other.

### Stage E — shadow

Every accepted signal is `status: shadow`. `datainsights/domain_registry.py`
reads `domains_fdm.yaml` and nothing else, so `assemble()` cannot resolve
a shadow signal's category and it **cannot enter a Recommendation or an
RM worklist**.

This is the load-bearing safety property, tested by
`test_shadow_signals_are_invisible_to_the_assembler`. Verified on the
real run: all three discovered signals resolve to `ADVISORY_ONLY` in the
production registry despite being proposed as `TREASURY_OPPORTUNITY`.

Promotion is a deliberate human edit, or a `backtest.py` hit rate once RM
outcomes exist.

---

## What was actually run

`python -m onboarding.discover_signals --profile legacy_local`, against
real generated legacy data (293,660 balance rows, 302,990 transactions,
606 accounts), as-of 2025-07-04 / 2025-10-02 / 2025-12-31:

- **28** candidates enumerated after Gate-1
- **3** passed screening
- **25** rejected, with reasons — e.g. balance deviation fires on 99.9%
  of clients: *"a threshold, not a signal"*
- **1** candidate combination rule by co-occurrence (lift 2.61)
- **3** proposals from qwen2.5:7b, all `TREASURY_OPPORTUNITY`, conf 0.75
- **3** accepted to shadow, names disambiguated

### Two things the run taught

**qwen2.5:7b could not fill a 7-field schema.** It reasoned correctly in
prose — right category, sensible caveats — then failed to invoke the
structured output tool on every candidate (`StructuredOutputException`).
Reducing to 5 fields with explicit `Field(description=...)` and a
worked example fixed it. `why_now` is now derived from screening
numbers rather than asked for. Noted because every other proposer in
this repo uses the same 5-field-ish shape, and now it is clear that is
not a coincidence.

**Three candidates came back with the same name.** They differ only in
window/k, so the model naming them all `transaction_amount_deviation` is
reasonable — but accepting them would have silently dropped two.
`signal_accept.py` now disambiguates by appending the differing
parameters.

### A bug the design caught

`run_spec()` originally passed `as_of` only to the proximity and absence
archetypes. The row-scanning ones therefore flagged December rows while
replaying July. Stage B's as-of check caught it immediately — 6,555
leaked signals on the first run. Fixed by clipping the frame in
`run_spec()` before any archetype sees it, so **every** archetype is
as-of correct. This is the class of bug the project's leakage discipline
exists to prevent, and the guard worked.

---

## Multi-backend execution (NOT BUILT)

The archetypes are UDF-free specifically so the same spec can run
elsewhere. Sketch only:

| Executor | Target | Mechanism |
|---|---|---|
| `pandas` | local CSV | today's path, built |
| `sql` | Snowflake, Athena | spec → SQL, pushed down |
| `spark` | Glue, EMR | spec → DataFrame ops |

Three known difficulties:

1. **`raw_measure` is the real conformance surface** — not "did it fire"
   but "are the numbers behind it identical".
2. **Median exactness varies.** pandas exact, Snowflake `MEDIAN` exact,
   Spark `percentile_approx` not. `median_mode` records which ran; the
   pandas executor *rejects* `approx` rather than silently returning
   exact results.
3. **IsolationForest cannot push down.** Deterministic baselines can;
   SLOT E2 needs `applyInPandas()` or a Snowpark UDF.

The precedent to copy is `tests/test_sql_source_conformance.py`, which
proves `SqlSource` matches the local source through DuckDB with no
credentials. Same trick would make the portability claim checkable.

**Snowflake has still never executed a query from this repo.**

---

## The honest limitation

Discovery establishes that a pattern is **novel**. It cannot establish
that it is **valuable**. With zero RM outcome labels, nothing can say
whether a candidate is predictive or an artefact of 606 synthetic
accounts.

That is why Stage E exists. The asymmetry matters: a wrong *baseline*
degrades detection on a known signal; a wrong *signal* invented from
noise fabricates an entire recommendation category that should never
have existed.

So: discovery finds candidates now, labels confirm them later, and
nothing reaches an RM in between.

---

## Files

**New (nothing existing modified):**

```
detection_engine/specs/__init__.py
detection_engine/specs/archetypes.py
onboarding/reference_specs.py
onboarding/signal_enumerator.py
onboarding/signal_screener.py
onboarding/signal_proposer.py
onboarding/signal_accept.py
onboarding/discover_signals.py
config/domains_discovered.yaml        (written by the run)
tests/test_signal_specs.py            (14 tests)
tests/test_signal_discovery.py        (20 tests)
```

Suite: **767 passed**, zero regressions.

## Running it

```bash
# stages A+B only, no LLM
python -m onboarding.discover_signals --profile legacy_local --no-llm

# with stage C (needs local Ollama)
python -m onboarding.discover_signals --profile legacy_local --max-proposals 3
```

Neither writes config. Acceptance is a separate explicit call to
`onboarding/signal_accept.py::accept(proposals, accepted_by=...)`.

## Scope: the full recommendation set, and what was built

The design was agreed as 15 numbered recommendations. Implementation was
scoped to **1-8**. Recommendations 9-15 were never started — they are not
abandoned or blocked, just out of the agreed scope.

Note the word "stage" is overloaded: recommendations 4-8 correspond to
pipeline stages A-E. "Stage 8" and "recommendation 8" are the same thing
(shadow mode); "stage E" is also that thing. Recommendations 9-15 have no
letter.

### Built (recommendations 1-8)

| # | Recommendation | Where |
|---|---|---|
| 1 | Collapse 9 detectors into 5 archetypes | `detection_engine/specs/archetypes.py` |
| 2 | A detector is a YAML spec, not a Python module | same |
| 3 | Declare the `raw_measure` contract per archetype | same, `Archetype.raw_measure_keys` |
| 4 | Stage A — enumerate candidates deterministically | `onboarding/signal_enumerator.py` |
| 5 | Stage B — screen for novelty, not value | `onboarding/signal_screener.py` |
| 6 | Stage C — Ollama proposer, validate-or-reject | `onboarding/signal_proposer.py` |
| 7 | Stage D — human acceptance gate | `onboarding/signal_accept.py` |
| 8 | Stage E — shadow mode before any RM sees it | same, + `config/domains_discovered.yaml` |

Recommendation 9 ("no new categories in v1") is a *constraint*, and it is
honoured: `signal_proposer.py` rejects any category not registered in
`config/categories.yaml`. It required no separate work.

### Not built (recommendations 10-15, multi-backend execution)

None of these were in scope. All concern running the same spec somewhere
other than pandas:

| # | Recommendation | Note |
|---|---|---|
| 10 | One spec, three executors (pandas / sql / spark) | only pandas exists |
| 11 | Keep all five archetypes UDF-free | **already satisfied** by the built archetypes — this constrained the design rather than requiring code |
| 12 | Declare median `exact`/`approx`, record which ran | **partly built**: `median_mode` is declared, recorded on every Signal, and `approx` is *rejected* by the pandas executor. The approx path itself awaits a pushdown executor |
| 13 | Mark which baseline a spec uses, so the planner knows if pushdown is possible | not built |
| 14 | Executor selection is a planner, not a config flag | not built |
| 15 | Conformance suite across executors | not built. Copy `tests/test_sql_source_conformance.py`'s approach |

**Snowflake has still never executed a query from this repo.**

### Also not built (beyond the 15)

Gaps found during implementation, not part of the original set:

- ~~A dashboard tab for stage D~~ — **built**: `dashboard/tabs/discover.py`,
  a "Discover signals" page under WORK. Enumerate + screen, review what
  survived and why each rejection happened, accept into shadow with the
  reviewer's name recorded. Also surfaces the shadow register.
- **Promotion tooling** — moving a shadow signal to `domains_fdm.yaml` is
  still a manual edit. Deliberately unautomated for now: promotion is the
  step that lets a machine-proposed rule reach an RM.
- **Combination-rule proposals** — co-occurrence lift is computed, reported
  in the CLI and shown on the page, but nothing proposes the rule *text*.
- **Category proposals** — out of scope by recommendation 9. A new NBA
  category is a governance decision about what the bank offers, not
  something a local model should mint. Adding one is a `categories.yaml`
  edit today.

### Two rejected options, recorded so they are not revisited by accident

| Rejected | Why |
|---|---|
| LLM generates detector *code* | Demos faster; unauditable and unmergeable in a bank. The value of the design is that a recommendation traces to a rule a human can read |
| Port the existing 9 detectors as a prerequisite | Unnecessary. Spec and Python detectors coexist; port later if useful, proven by golden-equivalence tests the way R23 did |

---

## Model selection for the proposer (measured)

Stage C's output becomes pipeline configuration, so it must reliably fill
a structured-output schema. That is a stricter requirement than narration,
where prose is validated in Python afterwards.

Probed against the real proposer schema, 4 runs each, on two different
candidates (a deviation rule and a dormancy rule), all local Ollama:

| Model | Structured output | Category stable | Notes |
|---|---|---|---|
| `llama3.1:8b` | **4/4 both candidates** | yes | ~2.4s. Rated the weak candidate 0.12 and the strong one 0.80 — it discriminates |
| `gpt-oss:20b` | 4/4 both candidates | yes | ~7-9s, ~3x slower; rated the weak candidate 0.90, which is worse judgement |
| `qwen2.5:7b` | **0/4 both candidates** | — | Reasons correctly in prose, then fails the tool call. A single earlier success was luck |
| `mistral:7b` | 0/4 | — | Emits correct JSON as text; does not support ToolChoice |
| `deepseek-r1` | 0/4 | — | Reasoning model, returns markdown; ~52s |
| `llama3.2:3b` | works, poor | — | Confidence 0.00, generic names |

So the profiles set `llm.task_models.proposer: llama3.1:8b` while
`llm.model` stays `qwen2.5:7b` for narration. Observable effect: with
qwen2.5 the three surviving candidates all came back under ONE name
(`transaction_amount_deviation`); with llama3.1 they are named distinctly
per parameter set.

This does not weaken R20 (`tests/test_model_id_single_source.py`): that
rule forbids a model tag in **Python**, and every tag still lives in a
profile. The `-cloud` refusal applies to per-task overrides too, so an
override cannot become a route around the cost policy.

**Not claimed:** that llama3.1:8b produces *better business judgement*.
What was measured is reliability of the structured call and discrimination
between a strong and a weak candidate. Judgement quality needs RM outcome
labels, which do not exist.
