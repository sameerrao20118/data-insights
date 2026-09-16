# Gap analysis — current state vs. the FDM-aligned + agentic build

Read `docs/decision_record.md` and `docs/agentic_plan.md` first; this
table is meaningless without them. Updated whenever a build session
changes what's true. "Priority" is a recommendation, not a commitment —
re-order it if your judgment differs.

| Gap | Current state | What's needed | Priority |
|---|---|---|---|
| Risk domain: `pd_migration` | Detector built and tested (`tests/test_risk_detectors.py`), not wired into `agents/tools.py` or `config/domains_fdm.yaml` | `PARTY_METRIC` data, which doesn't exist in the generated dataset or `config/entities_fdm.yaml`'s contract — not invented, per `docs/adding_a_new_domain.md`'s own rule | Blocked, not urgent — `rating_downgrade` already proves the wiring pattern works |
| A2 investigator reasoning consistency | Live run proposed a category contradicting its own stated evidence; validation checks the category is in the allowed set, not that reasoning supports it | A `_direction_problem`-style consistency check (`agents/domain_agent.py` already has the pattern) | **High — a live, disclosed bug, not a hypothetical** |
| SLOT E2 ML baseline value | Measured, not assumed: at 300-client/4yr scale, the challenger disagrees with the deterministic baseline on ~17% of `revenue_pattern_change` agreements; no real outcome labels exist to say which is right | Real resolution data (RM engagement outcomes) to check either baseline against — structurally can't be closed with synthetic data | Not started — accepted limitation, not a task |
| Treasury domain | No detector, no data | No confirmed C&I client-facing treasury data source exists at all (decision record's own Phase 6 finding) | Blocked on the bank, not a build gap |
| Real Snowflake connection | Adapter code exists (`FdmSnowflakeSource`, same `DataSource` interface), never executed | Your account-side credentials/VDI access | On you, not a build gap |
| Golden conformance: local vs. Snowflake | Not built | Needs `FdmSnowflakeSource` to have actually run once, so there's something to diff against | Blocked on the row above |
| AgentCore governance (Phases 1–5) | Not started — scaffold exists (`agents/model_factory.py`, `entrypoint.py`, `Dockerfile`), all contract-only | AIRA classification, Model Risk Owner assignment, and every other Phase-1 gate in `agents/README.md`'s table | Blocked on the bank, not a build gap |
| Real MIMO/Pega/S3 publish | `datainsights/sinks/mimo_placeholder.py` and `pega_event_mock.py` are schema-only, never wired | Phase 0/1 stakeholder conversations (MIMO AIEngine, C&I Decisioning) that only happen on the NatWest VDI | Blocked on the bank, not a build gap |
| A5 — propensity model (SLOT E4) | Correctly not started — `UnavailablePropensityModel` raises by design | RM-response label access, which is genuinely blocked per D6, not merely unbuilt. `datainsights/rm_feedback.py` (A3) is now capturing the label going forward, but historical volume + access are still missing | Gated, not a task to schedule |
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
