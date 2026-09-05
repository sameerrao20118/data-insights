# Gap analysis — current state vs. `objective.md`

Read `objective.md` first; this table is meaningless without it. Updated
whenever a build session changes what's true. "Priority" is my
recommendation, not a commitment — re-order it if your judgment differs.

| Objective component | Current state | Gap | Priority |
|---|---|---|---|
| Endogenous detection | 1 of 8 designed trigger types has a real detector (`large_incoming_payment`) | 7 trigger types (cash-flow stress, FX exposure, treasury surplus, counterparty concentration, credit utilization, dormancy, recurring revenue) are labeled in the data but nothing reads them | Medium — each is a repeat of an already-proven pattern, not new architecture |
| Exogenous detection | Not started before this session | Needs: event simulation, an `ExternalEventSource` interface, a sector/country matching detector, evidence shape for narrative | **High — this session's build** |
| Source flexibility (exogenous) | N/A | A pluggable interface so a real market/news/social API can implement the same contract later | **High — directly requested, built this session as `SimulatedExternalEventSource`** |
| Ranking across both categories | Explicit magnitude+recency formula, transaction-detector-shaped only | Needs a shared or parallel ranking path for exogenous evidence (severity in place of MAD-multiples) | Medium — built a parallel path this session rather than forcing one schema on two different kinds of evidence |
| Narrative across both categories | Validated template + Ollama narrator, transaction-evidence-shaped only | Needs an extraction-shaped evidence packet and validator for exogenous events (no `flagged_amount`, no per-client baseline — different facts to check) | Medium — built a parallel narrator this session, same validate-or-fallback pattern |
| Review/tracking (either category) | Flat markdown digest, no state beyond raw detection rows | No "reviewed/actioned/dismissed" status, no dashboard | Not started — flagged in an earlier conversation, still open |
| Incremental/replay monitoring | `--as-of` gives one static point-in-time cut | No checkpoint/watermark, no resumable incremental run | Not started — flagged earlier, still open |
| Trend/regime drift in simulation | Sector-based seasonality exists (summer/harvest/Q4/winter peaks) | No genuine regime shift over time (a sector downturn, a growth transition) — flagged earlier | Partially addressed this session: the exogenous event simulator embeds dated shock scenarios; transaction-level regime drift in `generate_data.py` itself is still open |
| Real-source extensibility (market trends, stock data, news, social feeds) | N/A before this session | Interface exists (`ExternalEventSource`); zero real adapters implemented | **This session delivers the interface + one clearly-marked extension point per real source category (see `external_events/README.md`)** |
| Revenue/ROI proof | Not possible without real outcome data | Structurally can't be closed with synthetic data — accepted limitation, not a task | Out of scope, per `objective.md` |
| Live Snowflake connection | Adapter code exists, never executed | Needs your account-side setup (`docs/snowflake_setup.md`) | On you, not a build gap |

## What this session's build actually closes

The three **High** rows above: an exogenous event category with a working
simulator, a source interface designed for real-API extension, and a
detector + ranking + narrative path that produces a demonstrable scenario.
See `external_events/README.md` for what was built and
`external_events/demo_scenario.py` for the runnable demonstration.

## What's still open after this session

Review/tracking dashboard, incremental/replay monitoring, transaction-level
regime drift, and the 7 remaining endogenous detectors are all real,
named gaps — none of them silently dropped, none of them claimed as done.
