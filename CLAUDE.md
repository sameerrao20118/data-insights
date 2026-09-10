# DataInsights — durable project instructions

Commercial/institutional banking NBA/EBM POC. Local Ollama only, no paid
LLM/cloud calls authorized. Snowflake is a configurable first source, not
a mandatory compute or AI platform — later sources (Glue/Iceberg/Athena,
AWS deployment) must not require rewriting domain logic.

For current status, what's built/verified/NOT RUN, and how to run it:
see `docs/current_state.md`. For the pipeline and module layout: see
`docs/architecture.md`. Full original requirements background:
`docs/DataInsights_Claude_Project_Prompt.md`.

## Hard boundaries (apply to every session, every phase)

- **Never read, load, or reason over ground-truth labels.** This means
  `**/protected_evaluator_only/**` (currently
  `data_generator/output/protected_evaluator_only/trigger_events.csv` and
  the holdout equivalent), any hidden generator injection logic, or
  label-table contents. Enforced both in code (`OfflineLocalSource`
  refuses these paths) and via `.claude/settings.json` deny rules — treat
  both as required, not either/or. If you need schema info that's mixed
  with label-generation code, ask for a sanitized extraction instead of
  reading the file.
- **No paid model or cloud calls.** No Snowflake Cortex/AI_COMPLETE, no
  Bedrock, no hosted judge/embedding endpoint, no paid fallback. Local
  Ollama only (`OLLAMA_NO_CLOUD=1`, no remote routing). If Ollama is
  unavailable, fall back to the deterministic template — never to a paid
  provider.
- **No production deployment or external communication.** No emails, no
  CRM writes, no AWS resource provisioning, no converting the Snowflake
  trial to paid. AWS/Bedrock/AgentCore adapters are contract-and-mock only
  until explicitly authorized; mark real integration as NOT RUN until it
  actually runs.
- **Keep source/detector/ranking/narrative/evaluator roles separate.**
  The detector and narrator never see ground truth; only
  `evaluation/evaluate.py` does, and it does not feed back into detection
  logic automatically. A repair/coding loop must not edit protected tests,
  labels, or acceptance criteria to make something pass.
- **As-of correctness.** Baselines and features must only use data
  available at decision time — no future leakage into detectors or
  backtests.

## Working style

- Continue from existing work; don't rebuild the generator, don't impose
  a new framework without evidence something is missing.
- Separate verified facts / assumptions / unavailable evidence / executed
  results explicitly in any report back. Never claim a cloud run,
  benchmark score, or credential succeeded without having actually run it.
- One phase/milestone at a time (see `docs/current_state.md` for what's
  next) — not a full re-implementation of every later phase.
- Config/secrets stay out of prompts, logs, notebooks, and commits.
