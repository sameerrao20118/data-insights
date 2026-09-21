# Handoff: signal discovery (stages 1-8 complete)

**Commit:** `b8a3b02` on branch `Release1`
**Full design:** `docs/signal_discovery_design.md`

This note carries the context that lived in the working session, so
picking up on another machine does not require re-deriving it.

---

## Where things stand

Stages 1-8 are built and run. The suite is green (767 passed, zero
regressions). Nothing is promoted to production: the three discovered
signals are `status: shadow` and cannot reach an RM worklist.

| Stage | Status |
|---|---|
| 1-3 archetype specs, executors, `raw_measure` contract | done |
| 4 enumerate | done |
| 5 screen | done |
| 6 propose (local Ollama) | done, run for real |
| 7 accept | done |
| 8 shadow | done, boundary verified |

---

## Getting set up on the new machine

```bash
git fetch && git checkout Release1 && git pull
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q -k "not live"     # expect 767 passed
```

Two environment notes that cost time in the original session:

- **The remote URL contains a literal `YOUR_TOKEN` placeholder.** A push
  or clone will fail auth until that is replaced with a real credential.
- **Generated data is not in git.** If `data_generator/output/` is
  absent, `python -m data_generator.generate_data` recreates it. Tests
  that need it skip cleanly when it is missing, so a green suite does not
  by itself prove the data is there.
- **Ollama** is only needed for stage C. Everything else runs without it;
  pass `--no-llm`.

---

## Reproducing the run

```bash
# stages A+B, deterministic, no model needed
.venv/bin/python -m onboarding.discover_signals --profile legacy_local --no-llm

# with stage C -- needs `ollama serve` on 127.0.0.1:11434
.venv/bin/python -m onboarding.discover_signals --profile legacy_local --max-proposals 3
```

Neither writes config. Acceptance is deliberately a separate call:

```python
from onboarding.signal_accept import accept
accept(proposals, accepted_by="your.name@bank")
```

Expected output on legacy data: 28 candidates, 3 pass screening, 25
rejected with reasons. If those numbers move a lot, the generated dataset
differs from the one used here.

---

## What is load-bearing (do not quietly change)

**`config/domains_fdm.yaml` is hand-authored and tooling must never write
it.** Discovery writes `config/domains_discovered.yaml` instead, so a
reviewer can diff machine proposals against human ones. Guarded by
`test_tooling_never_writes_to_the_handwritten_domains_file`.

**Shadow signals must stay invisible to `assemble()`.**
`datainsights/domain_registry.py` reads `domains_fdm.yaml` and nothing
else. If someone "helpfully" merges the discovered file into that
registry, machine-proposed signals immediately start reaching RM
worklists with no human approval. Guarded by
`test_shadow_signals_are_invisible_to_the_assembler` — that test failing
means the design is unsafe, not that the test is wrong.

**An unregistered category is rejected, never coerced.** If the model
invents a seventh category, `signal_proposer.py` drops the proposal.
Coercing it to `ADVISORY_ONLY` would hide the failure.

**Screening measures novelty, not value.** With zero RM outcome labels,
nothing can say whether a discovered signal is predictive. Do not let
the language drift toward "validated" or "proven" — the design's honesty
depends on that distinction.

---

## Known sharp edges

**qwen2.5:7b and structured output.** A 7-field Pydantic schema made the
model reason correctly in prose and then fail the tool call on every
candidate (`StructuredOutputException`). The schema is now 5 fields with
explicit `Field(description=...)` plus a worked example in the prompt.
If you add a field, re-run stage C before assuming it still works.
Every other proposer in this repo uses a similar small shape.

**Name collisions are expected.** Candidates differing only in window/k
come back under one business name. `signal_accept.py` disambiguates by
appending the differing parameters. Do not "fix" this by skipping
duplicates — that silently drops rules a reviewer approved.

**`median_mode: approx` is deliberately rejected** by the pandas
executor. It exists to be recorded by a future pushdown executor that
cannot compute an exact median. Silently returning exact results would
make a cross-backend difference invisible.

**Observation-grain concepts need a party_id join.**
`BalanceObservation` and `Transaction` are keyed by `account_id` only;
`discover_signals.py` joins `party_id` from `Account`. This mirrors what
the hand-written detectors require of their caller (see
`cash_buildup.py`'s docstring).

---

## Suggested next steps

Roughly in order of value:

1. **Dashboard review tab for stage D.** Acceptance is currently a Python
   call. A tab showing proposals with their screening numbers, and a
   button that calls `accept(...)`, is the natural home — likely
   alongside "Onboard a source" or "ML opportunities".
2. **Combination-rule proposals.** Co-occurrence lift is already computed
   and reported (`signal_screener.co_occurrence`), but nothing proposes
   the rule text. This is a smaller version of what `signal_proposer.py`
   already does.
3. **Promotion tooling.** Moving a shadow signal to `domains_fdm.yaml` is
   a manual edit today. Worth automating only with a reviewer gate at
   least as strict as acceptance.
4. **SQL/Spark executors.** Design sketched in
   `docs/signal_discovery_design.md`. The conformance-test approach to
   copy is `tests/test_sql_source_conformance.py`, which proves
   `SqlSource` matches the local source through DuckDB with no
   credentials. Snowflake has still never executed a query from this
   repo.
5. **Porting the nine hand-written detectors to specs.** Optional and not
   urgent. If done, prove it with golden-equivalence tests the way R23
   did — the reference specs in `onboarding/reference_specs.py` are
   approximations for novelty baselining, NOT drop-in replacements
   (they omit per-currency floors, cooldown bookkeeping, SLOT E2 baseline
   switching, and `insufficient_evidence` rows).

---

## Map of the new code

```
detection_engine/specs/
  archetypes.py          5 archetypes + run_spec() + as-of clipping
  discovered/*.yaml      3 accepted shadow specs

onboarding/
  signal_enumerator.py   stage A -- candidates, Gate-1 pruning
  signal_screener.py     stage B -- fires/stable/novel/as-of + co-occurrence
  signal_proposer.py     stage C -- Ollama, validate-or-reject
  signal_accept.py       stages D+E -- writes shadow config
  reference_specs.py     the 9 detectors as specs (novelty baseline only)
  discover_signals.py    CLI driver for A->C

config/domains_discovered.yaml   machine-written, all shadow
tests/test_signal_specs.py       14 tests -- archetypes, as-of, contract
tests/test_signal_discovery.py   20 tests -- stages A-E, shadow boundary
```

Review artifacts land in `onboarding/proposals/signals/`, which is
gitignored (consistent with how the binding proposer's output is
treated) — regenerate by re-running the CLI.
