# Adding a new domain to the FDM-aligned build

The pattern below has been proven twice (Deposits, Lending) plus a third
non-detector variant (Exogenous). This is the checklist a future
contributor — or a future you, on the NatWest VDI with real schema access
— should follow to add Risk, Treasury, or anything else, rather than
re-deriving the shape from scratch.

**Extensibility principle this whole build is built on**: every layer
above the data source reads through `datainsights/sources/base.py`'s
`DataSource` interface, or the `Signal`/`Recommendation` dataclasses, and
never touches a file path, a schema name, or a Snowflake connection
directly. This is not new — it's the same discipline
`OfflineLocalSource`/`SnowflakeSource` already prove for the legacy
pipeline. `FdmLocalSource` does the FDM-shaped equivalent.

**This now holds for the agent/correlation layer too (M7, the domain
registry).** Before M7, adding a domain meant editing four shared Python
structures directly — `agents/tools.py`'s inline product codes,
`agents/orchestrator.py`'s `DETECTOR_BY_TOOL` dict, `agents/domain_agent.py`'s
`ALLOWED_ACTIONS_BY_DOMAIN`, and `datainsights/correlation/hypothesis.py`'s
`ENDOGENOUS_CATEGORY`/`ENDOGENOUS_HYPOTHESIS`. Steps 5 and 6 below now
describe registering into two small registries instead — neither
`orchestrator.py`, `domain_agent.py`, nor `hypothesis.py` needs editing
to pick up a new domain; `tests/test_domain_registry.py`'s dummy-domain
test is the proof.

## 1. Contract the entities (`config/entities_fdm.yaml`)

For each new physical table:
- `physical_table`, `domain` (a new domain tag — this determines the
  folder/schema it lives in, see step 2), `grain`, `primary_key`,
  `required_columns` (typed, with `allowed:` value sets where known).
- If it's bi-temporal (most FDM tables are — see
  `docs/decision_record.md`'s "bi-temporal is load-bearing"), it needs
  `EFFECTIVE_START_DT`/`EFFECTIVE_END_DT` in `required_columns`.
- Mark provenance honestly: real captured DDL, real DDL with invented
  values, or a fully invented table (no DDL captured at all) — see the
  file's own header for exactly how PARTY vs. PARTY_DEMOGRAPHIC vs.
  COLLATERAL_ITEM are each labeled differently.
- If a table's DDL genuinely doesn't exist yet (Risk/Treasury today),
  **don't invent it silently** — document it as deferred in the file
  header with a reason, the way `PARTY_METRIC`/`PARTY_RELATED`/CRADLE
  `*_MODEL_DATA` and every TILAPI/treasury entity are today.

## 2. Give the domain a physical (or schema) home

`config/entities_fdm.yaml`'s `domain:` tag is not decorative —
`FdmLocalSource._csv_path()` resolves every read as
`{data_dir}/{domain}/{physical_table}.csv`. Today there are two:
`kernel/` (Tier 1 FDM classes used everywhere — PARTY, AGREEMENT,
EVENT_FINANCIAL) and `lending/` (Tier 3 domain-specific extensions —
MORTGAGE_AGREEMENT, COLLATERAL_*). A new domain (`risk/`, `treasury/`)
is a new subfolder plus new entries in this tag — nothing else needs to
change in `FdmLocalSource` itself.

**This is the extensibility point for Snowflake.** A future
`SnowflakeSource`-equivalent for the FDM path honors the exact same
`domain` field, mapping it to a schema or database instead of a folder
(e.g. `kernel` → `ENT_PRD.TIER0_PRS`, `lending` → a domain-specific NPDM
schema, a new `risk` domain → wherever CRADLE data actually lives). As
long as the new source implements the same method names
(`party()`, `agreement()`, `as_at()`, `read_entity()`, ...) against the
same entity contract, every detector, agent, and correlation function
above it needs zero changes — this is the same guarantee
`config/entities.yaml` + `OfflineLocalSource`/`SnowflakeSource` already
provide for the legacy pipeline. **Do not let a new domain module import
`duckdb`, a file path, or a Snowflake connection directly — always go
through the `FdmLocalSource` instance passed in.**

## 3. Generate synthetic data for it (or don't, honestly)

`data_generator/fdm/generate_fdm.py` is the reference implementation:
reuse its `--seed`/Faker convention (42 dev, 1337 holdout), write into
the new domain subfolder, and if any entity is bi-temporal, generate
**multiple effective-dated versions** for at least one key — a
current-state-only table silently defeats every as-at test built on top
of it (see `config/entities_fdm.yaml`'s COLLATERAL_ITEM_VALUE note for
why this specific mistake matters).

If real DDL genuinely isn't available yet (today's situation for Risk),
**don't generate a plausible-looking dataset just to have one** — leave
the domain unbuilt and documented as deferred, the way this build does.
A `NotImplementedError` with a clear reason (see `FdmLocalSource.
risk_measure()`/`treasury_position()`) is more honest than a synthetic
table nobody asked you to invent.

## 4. Write the detector(s) (`detection_engine/<name>.py`)

Every detector in this build (`cash_buildup.py` is the shortest,
`collateral_coverage_drop.py` the most complex) follows this exact
shape — copy the closest match and adapt:

```
@dataclass(frozen=True)
class DetectorConfig:
    ...                              # thresholds, cooldown_days, rule_version
    @classmethod
    def from_rules_dict(cls, rules: dict) -> "DetectorConfig": ...

DETECTION_COLUMNS = [...]           # every column the detect() output carries

def detect(df: pd.DataFrame, config: DetectorConfig, run_id: str, *maybe_as_of) -> pd.DataFrame: ...
def apply_cooldown(detections: pd.DataFrame, config: DetectorConfig) -> pd.DataFrame: ...
def to_signal(row: pd.Series) -> Signal: ...   # detection_engine/signal.py
```

Two families exist, pick whichever fits the business question:
- **Rolling/time-series** (`cash_buildup`, `revenue_pattern_change`,
  `facility_utilization_spike`) — evaluates every historical row,
  strictly-prior-only baselines (no future leakage — see
  `large_incoming_payment.py`'s `_rolling_median_mad` for the canonical
  no-leakage technique).
- **Point-in-time snapshot** (`dormancy`, `facility_maturity_approaching`,
  `fixed_rate_expiry`, `collateral_coverage_drop`) — takes an explicit
  `as_of: date` and answers "is this true right now."

Add its config section to `config/rules.yaml` under the shared
`fdm_rule_version` key (not the legacy pipeline's `rule_version` — that
naming mistake happened once already in this build across all 7
detectors; don't repeat it).

Write its test file the same way the existing 7 do: hand-built
`pd.DataFrame` fixtures (never generator output, never
`protected_evaluator_only/`), covering the boundary/insufficient-evidence
case, idempotent `detection_id`, cooldown suppression, and — if
bi-temporal — an explicit as-at test proving history is read correctly
(`test_collateral_coverage_drop.py` is the reference for this).

## 5. Expose it as an agent tool, then register it (`agents/tools.py`)

Add `make_<domain>_tools(source, rules, as_of) -> list` following
`make_deposits_tools`/`make_lending_tools`: each detector becomes one
`@tool`-decorated closure taking only `prty_id`, fetching its own data
slice, running the unmodified `detect()`, and returning a JSON-safe dict
**that includes `event_date`** (a real bug this build hit once — the
first version of every tool omitted it, breaking the `to_signal()`
handoff silently until an end-to-end test caught it). If the domain has
no detector at all (like Exogenous), wrap whatever deterministic function
answers its question instead — see `make_exogenous_tools` wrapping
`exposure_qualifier.qualifies()`. Product/agreement type codes (`DEP`,
`LON`/`ODR`, ...) come from `datainsights.domain_registry.product_codes()`,
not an inline list — see step 6a.

Then, at the bottom of `agents/tools.py`, one `register()` call
(`agents/domain_registry.py`'s `DomainSpec`) — this is the whole "code"
half of the registration:

```python
register(DomainSpec(
    name="<domain>",
    make_tools=make_<domain>_tools,
    detector_by_tool={"check_x": detector_module_x, ...},  # {} if no Signal-producing tool
))
```

`agents/orchestrator.py` reads `all_specs()`/`detector_by_tool()` from
this registry generically — nothing there names a domain. The one
documented exception is "exogenous": its factory takes an extra `event`
argument (a specific external event to check exposure against), so
`orchestrator.py` calls it explicitly rather than through the uniform
`(source, rules, as_of)` loop — see `make_exogenous_tools`'s own
docstring and `evaluate_client`'s comment.

## 6. Wire allowed actions + category/hypothesis (`config/domains_fdm.yaml`)

This is the "data" half — no Python file to edit. Add a block to
`config/domains_fdm.yaml`:

```yaml
<domain>:
  product_codes:
    <group>: [CODE, ...]        # step 5's product_codes() lookup reads this
  allowed_actions:
    - "RM to ..."
    - "No action -- monitor only"   # every domain's list ends here
  signals:
    <signal_type>:
      category: FINANCING_NEED      # one of the 6 NBA categories
      hypothesis: >-
        The general, event-type-level reasoning sentence -- not
        client-specific, that's what recommended_action is for.
```

`agents/domain_agent.py`'s `DomainAgent` and
`datainsights/correlation/hypothesis.py`'s `assemble()` both read this
file directly (`datainsights/domain_registry.py`), not via a rules dict
passed at call time — see that module's own docstring for why (in short:
`hypothesis.py` must give correct category/hypothesis text even when
imported with no `agents.*` import in the chain at all, as
`tests/test_correlation.py` does). If the new domain's category is
`RISK_REVIEW`, no further wiring is needed — rule 1 in `assemble()`
already suppresses revenue categories whenever any `RISK_REVIEW`-mapped
signal or `HIGH_RSK_CUST_IND=Y` is present, generically, for every
domain.

## 7. What must never change when adding a domain

- `datainsights/correlation/hypothesis.py`'s `HIGH_RSK_CUST_IND`
  suppress-only contract — a new domain's signals still flow through the
  same rule, never bypass it.
- The FinCrime boundary (`FdmLocalSource`'s refused-table list,
  `domain_agent.py`'s `BANNED_TERMS`) — no new domain ever reads
  `FSA_PRD_FINCRIME`/`PEP_PRS`/etc., full stop.
- The "no autonomous multi-step agent" constraint — a new domain's agent
  calls its own tools and narrates; it never calls another domain's
  tools, and it never decides the final category/score itself.

See `docs/decision_record.md` and `docs/fdm_reference.md` for the
underlying source material this pattern is built from, and
`docs/current_state.md`'s "FDM-aligned build" section for what's actually
built today versus deferred.
