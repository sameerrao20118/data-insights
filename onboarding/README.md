# Onboarding — the single page for "bring a new schema/data asset here"

Before this file, instructions for onboarding a new schema were scattered
across `README.md`, `docs/generalization_plan.md`'s Phase 5 section,
individual module docstrings, and the dashboard's own UI text — no one
place was authoritative. This is that one place. It covers two related
but separate things this package does:

1. **Onboarding a new physical schema** (a directory of CSVs) so the
   existing detectors/agents/correlation logic can run against it —
   `profiler.py` → `binding_proposer.py` → `accept.py`.
2. **Identifying ML opportunities within an already-onboarded schema**
   (which fields are worth a challenger baseline) — `ml_profiler.py` →
   `ml_measure_proposer.py`. See `docs/ml_quickstart.md` for that half
   specifically, in baby-steps form.

Design principle behind both: **propose, validate, human-accept.**
Nothing in this package ever writes to `config/` on its own — see
`accept.py`'s own docstring for exactly why that gate is mandatory, not
decorative.

---

## Part 1 — Onboarding a schema

### Baby steps

```bash
# 1. Profile the directory + propose a binding (local Ollama, one small
#    call per canonical concept)
python -m onboarding.propose <data_dir> --name <schema_name>

# 2. READ the review before touching anything else
cat onboarding/proposals/<schema_name>/review.md

# 3. If a mapping looks wrong, edit the proposal file directly
$EDITOR onboarding/proposals/<schema_name>/binding.proposed.yaml

# 4. Accept -- the ONLY step that writes to config/
python -m onboarding.accept <schema_name>
# add --force only to deliberately overwrite an existing schema of the same name
```

Or via the dashboard: the **Onboard a source** tab → the same three steps
as buttons, with the proposed YAML editable inline before you click Accept.

### What each module actually does

| Module | Does | Never does |
|---|---|---|
| `profiler.py` | Deterministic structural read of a CSV directory: columns, types, key candidates, bi-temporal pair detection. No LLM. | Guess meaning — only structure |
| `binding_proposer.py` | One local-Ollama call per canonical concept (`config/semantic_model.yaml`), proposing which table/columns map to it, with confidence + evidence | Trust its own output — every proposed column is checked against the real profiled columns; invalid ones are dropped into `rejected`, never silently kept |
| `entity_contract_generator.py` | Deterministic: turns a profile into a valid `config/entities_<name>.yaml`-shaped contract | Anything with an LLM in it |
| `proposal_writer.py` | Writes the 3 files under `onboarding/proposals/<name>/` (proposal YAML, generated contract, human-readable review report) | Touch `config/` |
| `accept.py` | Validates the (possibly hand-edited) proposal with the SAME `validate_binding()` every hand-written binding is held to, then — only on success — copies it into `config/bindings/` and `config/entities_<name>.yaml`. Refuses to silently overwrite. | Write anything on a failed validation |

### The honest limits, right now

- **Input shape**: CSV directories only. DDL text and Excel data
  dictionaries are real, scoped, undone work — every schema onboarded so
  far (`fdm`, `legacy`, `sba`) genuinely looked like a CSV directory, so
  that's the one shape this proves end to end rather than three
  half-built readers.
- **Exogenous events**: not covered here at all — `config/event_types.yaml`
  is still hand-written. A Gate-3-style proposer for event types is a
  natural extension, not built.
- **A newly onboarded schema's whole-book worklist**: `agents/demo_fdm_scenario.py`
  (the script that generates a full RM worklist) still calls a few
  FDM-specific `DataSource` methods internally — confirmed by actually
  running it against a non-FDM profile and watching it fail. A freshly
  onboarded schema is provably READABLE end to end (its binding validates,
  `CanonicalSource.read()` works, the sampled detector tools run) but
  does not automatically get a full batch worklist. See the dashboard's
  **Explore a source** tab, which is honest about exactly this for each
  schema.

Full, real, live-tested proof that this actually works end to end
(including the human catching two genuinely wrong LLM proposals):
`tests/test_onboarding_live.py`, run against real U.S. SBA data as a
blind test.

---

## Part 2 — Identifying ML opportunities

See `docs/ml_quickstart.md` for the full baby-steps walkthrough
(dashboard + CLI). Short version: `ml_profiler.py` answers "which fields
here have enough clean history to be worth an ML challenger" — the same
propose/validate discipline, but for measures instead of schema mappings.
It never decides a challenger should replace the deterministic default;
`config/ml_policy.yaml` and a real champion/challenger run
(`datainsights/ml/runner.py`) decide that, and a human always has the
final say via the dashboard's Save button.
