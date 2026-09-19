# Gap analysis — current state vs. the FDM-aligned + agentic build

Read `docs/decision_record.md` and `docs/agentic_plan.md` first; this
table is meaningless without them. Updated whenever a build session
changes what's true. "Priority" is a recommendation, not a commitment —
re-order it if your judgment differs.

| Gap | Current state | What's needed | Priority |
|---|---|---|---|
| **The generality gap itself** (detectors FDM-native, content in code, semantic model a constant, no trigger, agents narrate-only) | Fully evidenced in `docs/hardcoding_audit.md`; nothing fixed yet | `docs/refactor_plan.md` -- 12 tasks in 4 waves, start with R1-R3 | **High -- this is the register for everything below that is structural rather than a single feature** |
| Risk domain: `pd_migration` | Detector built and tested (`tests/test_risk_detectors.py`), not wired into `agents/tools.py` or `config/domains_fdm.yaml` | `PARTY_METRIC` data, which doesn't exist in the generated dataset or `config/entities_fdm.yaml`'s contract — not invented, per `docs/adding_a_new_domain.md`'s own rule | Blocked, not urgent — `rating_downgrade` already proves the wiring pattern works |
| A2 investigator reasoning consistency | Live run proposed a category contradicting its own stated evidence; validation checks the category is in the allowed set, not that reasoning supports it | A `_direction_problem`-style consistency check (`agents/domain_agent.py` already has the pattern) | **High — a live, disclosed bug, not a hypothetical** |
| SLOT E2 ML baseline value | Measured, not assumed: at 300-client/4yr scale, the challenger disagrees with the deterministic baseline on ~17% of `revenue_pattern_change` agreements; no real outcome labels exist to say which is right | Real resolution data (RM engagement outcomes) to check either baseline against — structurally can't be closed with synthetic data | Not started — accepted limitation, not a task |
| Treasury domain | No detector, no data | No confirmed C&I client-facing treasury data source exists at all (decision record's own Phase 6 finding) | Blocked on the bank, not a build gap |
| Real Snowflake connection | Adapter code exists (`FdmSnowflakeSource`, same `DataSource` interface), never executed | Your account-side credentials/VDI access | On you, not a build gap |
| Golden conformance: local vs. Snowflake | Not built | Needs `FdmSnowflakeSource` to have actually run once, so there's something to diff against | Blocked on the row above |
| AgentCore governance (Phases 1–5) | Not started — scaffold exists (`agents/model_factory.py`, `entrypoint.py`, `Dockerfile`), all contract-only | AIRA classification, Model Risk Owner assignment, and every other Phase-1 gate in `agents/README.md`'s table | Blocked on the bank, not a build gap |
| Real MIMO/Pega/S3 publish | `datainsights/sinks/mimo_placeholder.py` and `pega_event_mock.py` are schema-only, never wired | Phase 0/1 stakeholder conversations (MIMO AIEngine, C&I Decisioning) that only happen on the NatWest VDI | Blocked on the bank, not a build gap |
| A5 — propensity model (SLOT E4) | Correctly not started — `UnavailablePropensityModel` raises by design | RM-response label access, which is genuinely blocked per D6, not merely unbuilt. `datainsights/rm_feedback.py` (A3) is now capturing the label going forward, but historical volume + access are still missing | Gated, not a task to schedule |
| Per-client O(clients x rows) in `CanonicalSource` | `RunScopedCache` now wired into `build_runtime` (465 -> 63 ms/client, 98,063,596 -> 181,848 rows at 300 clients), but `CanonicalSource` still re-merges/re-projects the full table per client -- 860 reads re-assembling 2,373,916 rows for 60 clients | Invert the loop: a set-based whole-book path. Detectors are already set-based and `read()` already accepts no party_id; one pass measured 0.12s vs 1.80s for 300 clients. Blocked on proving equivalence on a POSITIVE-detection window (the 5 windows compared so far all found zero) | **High -- this is the remaining scale ceiling** |
| Predicate/aggregate pushdown to the source | `CanonicalSource.read()` pulls whole tables with `allow_unbounded=True` and filters in pandas | `party_ids`/`account_ids` params that become SQL WHERE; then a SQL-emitting path in `features.py` so Snowflake returns features, not rows (`docs/ml_strategy_plan.md` T6) | High for any real-data deployment |
| `check_facility_utilization` / `check_fixed_rate_expiry` produce zero detections | Found by `tests/test_detector_coverage.py`. Generator anchors both cohorts to END_DATE; demo as_of is ~9 months earlier, so the interesting events are in the future relative to evaluation | Re-anchor those two cohorts the way `cash_buildup` was (M16), one at a time so detections stay attributable | Medium -- two categories under-represented in every demo |
| ~~No RM entitlement in the FDM worklist~~ | **CLOSED, 2026-09-18.** `Party.relationship_manager_id` now flows through semantic model + `fdm.yaml` binding + generator (deterministic hash of PRTY_ID, zero rng draws, confirmed the whole random stream stayed aligned on regeneration) + `RM_WORKLIST_COLUMNS`. Live run: 36-row worklist, 16 distinct RMs, 0 empty ids. `legacy`'s binding degrades honestly (field simply absent) -- and its comment now flags that `clients.csv` genuinely HAS this data, just not contracted yet. | Contract `clients.csv` as a legacy Party-mappable entity so legacy gets the real field instead of degrading | Closed |
| ~~No self-service ML tooling~~ | **CLOSED, 2026-09-18** (`docs/ml_strategy_plan.md` T1-T5, verified in `docs/changelog.md` M17). A user can now: scan any schema for ML-eligible measures with a stated reason per rejection (`onboarding/ml_profiler.py`); set policy per measure with a human-only Save (`config/ml_policy.yaml`, `datainsights/ml/policy.py`); run a real champion/challenger comparison and read a manifest (`datainsights/ml/runner.py`) -- all from the dashboard's **ML opportunities** tab or the CLI (`docs/ml_quickstart.md`). | The champion/challenger runner only has a wired comparison for the `fdm` schema's 2 known E2 measures (`MEASURE_COMPARISONS` in `datainsights/ml/runner.py`) -- legacy/sba correctly report "not wired" rather than crash or fake a result, but extending real coverage to them is real, undone work | Medium -- self-service now exists, coverage is narrow |
| Aggregate pushdown only proven for `OfflineLocalSource` | `datainsights/features.py::compute_many()` pushes down to real DuckDB SQL for the flat-schema backend (`legacy`/`sba`), golden-tested to agree with the pandas fallback to < 1e-6 across mean/sum/count/last/first | `FdmLocalSource`'s bi-temporal as-at collapse needs the same treatment -- real complexity to replicate correctly in SQL, not attempted this pass (`docs/ml_strategy_plan.md` T6) | Medium |
| ML training backends (SageMaker/Snowpark) | Contract-only, both raise `NotImplementedError` naming their exact blocker (`datainsights/ml/backends/`) | Real wiring gated on: (1) RM-feedback label volume for E4 (still blocked on D6 access), (2) AWS/Snowflake credentials, (3) explicit authorization per `CLAUDE.md` -- none of which this repo can grant itself | Blocked on the bank, not a build gap |
| Replay/simulation with a virtual clock | `--as-of` gives one static point-in-time cut, which is the primitive this would be built from | Incremental checkpoint/watermark machinery (project instructions §10.1) | Not started |

## What's closed, for reference (not re-litigated here)

The FDM build's own four founding gaps (from `docs/decision_record.md`'s
"what genuinely does not exist") are closed and verified, not open
items any more:

- **Exogenous events** — closed structurally by `external_events/`, and
  M8's A1 (`event_extraction_agent.py`) closes the specific sub-gap of
  "nothing turns unstructured text into a validated event," verified
  with a real precision/recall measurement (TP=5, FP=0, FN=1 on 10
  hand-written notices).
- **Cross-domain correlation** — `datainsights/correlation/`, verified
  end to end against real generated data.
- **Hypothesis layer** — every `Recommendation` carries a `hypothesis` +
  sized `recommended_action`.
- **Growth-side endogenous signals** — `cash_buildup`,
  `revenue_pattern_change`, `facility_utilization_spike`, etc.

## Compliance/realism check

`docs/decision_record.md`'s D5 (FinCrime: read nothing) and the
geopolitical/PEP prohibition are enforced in code, not just documented —
`FdmLocalSource`'s refused-table list, `agents/domain_agent.py`'s
`BANNED_TERMS` check (reused by every M8 agent:
`investigator_agent.py`, `rm_copilot_agent.py`,
`event_extraction_agent.py`). No new boundary question was raised by
adding agents this session — every one of them is held to the identical
check, verified by test in each case.
