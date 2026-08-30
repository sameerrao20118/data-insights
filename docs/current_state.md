# Current state (as of 2026-08-30)

Live and runnable end-to-end on the `offline_ollama` profile (local Ollama,
no credentials, no paid calls). Snowflake is wired but **NOT RUN**.

## Run it yourself

```bash
source .venv/bin/activate
python -m datainsights.cli                        # full run, top 15 narratives (~60s first time, ~7s cached)
python -m datainsights.status                      # what happened last
python -m datainsights.judge.run_sample 10          # offline-sampled semantic judge
python -m evaluation.evaluate                       # dev-diagnostic precision/recall
python -m pytest tests/ -v                          # 9 detector tests
cat var/insights/digest_*.md                        # the actual RM digest output
```

## What's implemented and verified working

- **Data**: dev dataset (seed 42) + an independently-seeded holdout (seed
  1337), both with `trigger_events.csv` isolated under
  `protected_evaluator_only/`.
- **Config**: typed, validated profiles (`config/profiles/*.yaml`,
  `datainsights/config.py`). Hard-blocks paid calls, remote inference,
  Ollama `-cloud` model tags, and non-localhost endpoints at the code
  level, not just declaratively.
- **Source contract**: `config/entities.yaml` (transactions/accounts/
  balances only -- grain, PK, time/currency semantics, missing-data policy).
- **DataSource**: `OfflineLocalSource` (DuckDB-backed), tested against
  leakage (refuses `trigger_events`, refuses a protected-path root, refuses
  unbounded reads). `SnowflakeSource` skeleton exists, same interface,
  **NOT RUN** (no credentials).
- **Detector**: `detection_engine/large_incoming_payment.py` -- point-in-time
  correct (no future leakage, verified by test), 9/9 tests passing,
  idempotent, cooldown-suppressing. Spec: `docs/detector_spec_large_incoming_payment.md`.
- **Ranking**: explicit magnitude+recency formula, `datainsights/ranking.py`.
- **Narrative**: deterministic template baseline +
  local-Ollama structured-output narrator (qwen2.5:7b) with deterministic
  validation (schema, numeric consistency, banned-claim check) and
  fallback-to-template on any failure. Verified: a real failure case was
  caught and correctly fell back during development.
- **Judge**: offline-sampled, different model than narrator (llama3.1:8b),
  correlated-error caveat disclosed in code. Ran against 10 cached
  narratives: faithfulness mean 3.9/5 (min 2), 8/10 flagged for human
  review -- a genuine finding, not smoothed over. Worth reading those 8
  before trusting this narrator prompt further.
- **State**: SQLite, idempotent detections, narrative cache (evidence-hash
  keyed), run history. Verified idempotent on rerun (0 newly-seen on an
  unchanged snapshot).
- **Digest**: local markdown file under `var/insights/`, no delivery
  anywhere.
- **Evaluation**: separate module, reads the protected ground truth (its
  job, not the detector's). Dev-dataset result: precision 0.09 / recall
  0.66 vs. the narrow `TENDER_PAYMENT` label; holdout-dataset result:
  precision 0.14 / recall 0.83. Consistent pattern across both seeds. Low
  precision is expected and explained in evaluation/evaluate.py's
  docstring -- the detector's actual claim ("statistically large payment")
  is broader than that one injected label, so many detections are
  legitimate anomalies the generator never labeled as anything.
- **Status view**: `datainsights/status.py`.

## What's explicitly NOT built / NOT RUN

- Snowflake: no query has ever executed against your trial account.
- Replay/simulation mode with a virtual clock, and a real scheduler/monitor
  loop: not built this pass -- `run_once` with `--as-of` gets you a
  single point-in-time cut, which is the primitive replay would be built
  from, but the incremental-checkpoint/watermark machinery in project
  instructions section 10.1 isn't implemented yet.
- ML ranking challenger, retrieval, prompt optimization, AWS/Bedrock/
  AgentCore: correctly out of scope for this phase.
- A coding-process benchmark (project instructions section 9): not started.
- Golden conformance tests comparing OfflineLocalSource vs. SnowflakeSource
  output on identical data: not built (SnowflakeSource has never run, so
  there's nothing yet to diff against).

## Known limitations worth reading before trusting this further

- All development-time precision/recall/judge numbers come from a session
  that authored the injection logic. Contamination is disclosed everywhere
  it matters; don't cite these numbers as detection quality.
- Event-time replay only (booking_date doubles as detection-availability
  time) -- not true availability-aware backtesting.
- The MAD-multiplier threshold (6.0) and floor (5000 EUR-equivalent) are
  provisional defaults, not validated against any outcome.
- Judge/narrator correlated-error risk: both local Ollama models, unknown
  training-data overlap.
