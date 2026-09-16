# DataInsights — Engineering Decision Record (source capture)

Source: screenshots of an internal reference page (`DECISION_RECORD.html`,
"FDM-aligned, exogenous-event NBA enrichment for C&I", Rev 3, Sept 2026,
65KB, 7 tabs). Captured 2026-09-13. This file preserves that page's
content verbatim as reference/context for DataInsights work — it is
**not verified against live systems from this repo**, and describes a
broader enterprise landscape (Pega CDH, MIMO, NPDM, AgentCore, CRADLE)
that this repo's code does not itself implement or call. Treat it as
architectural/organisational context, not as a spec this repo currently
satisfies.

This is a living document — more of this page's tabs will be appended as
further screenshots are shared.

Page badges: **Local-first: Strands + Ollama VERIFIED** · **Route: MIMO
insight → Pega CDH** · **Then: AgentCore Runtime** · **Gap closed first:
exogenous events**.

Tab layout (7 tabs, each a distinct screenshot for a different audience,
per the page's own build note):

1. **Decisions** — the seven decisions as one table with ruling,
   rationale, and Confluence evidence, plus the three Rev-1 corrections
   and the four genuine gaps side by side. This is the tab for
   leadership.
2. **Landscape & Gaps** — the existing end-to-end flow as boxes, the
   domain coverage matrix colour-coded strong/partial/thin, and the
   Outflows NBA spec to match, including the response taxonomy.
3. **Architecture** — four layers drawn out, new work in green and reuse
   in grey. Exposure qualification called out as the thing that separates
   a signal from a mailing list. The FDM join backbone as runnable SQL.
4. **Output Schema** — MIMO insight record and ODS packet side by side
   with the Pega presentation layer, then the attribute key table with
   new/align status per row. Exogenous keys starred.
5. **Phasing** — eight numbered phase cards with colour-coded badges,
   each carrying its gate in amber, plus the AgentCore staging diagram
   and the five-phase governance table showing why Stage 3 is last.
6. **Extension Slots** — five slot types (A source domains, B detectors,
   C event sources, D output channels, E ML challengers) drawn as dashed
   placeholder boxes with the Protocol contracts. Every unfilled slot
   names its phase and what blocks it.
7. **Local Env** — the verified package table, the Strands provider list
   showing `ollama`/`llamacpp` in the box, machine specs, workload
   feasibility, and copy-paste setup.

On the extension slots specifically (a stated requirement): each slot is
a named Protocol with a typed signature, so adding a domain means
implementing an interface rather than touching the pipeline. Slot A2
(Treasury) is deliberately marked blocked with "source unknown" rather
than left implicit. Slots C-open cover the six exogenous sources beyond
the Phase 1 tender case. Slot D3 documents the DPPS route if output ever
becomes a governed data product.

Two things carried through as hard non-slots, because they're
architectural boundaries rather than backlog: no outbound channel of any
kind (Pega owns delivery), and no arbitration ranker (Pega adaptive
models own that).

Two items need action from a human rather than engineering: the Ollama
runtime isn't installed and gates all local narrative work (a software
request), and 2 logical cores with no GPU will make local inference slow
(worth asking for a higher-spec box or Kepler/SMUS workspace if one
exists) — neither blocks Phases 1-3.

---

## Tab 1 — Decisions

### Seven Decisions

| # | Decision | Ruling | Because | Evidence |
|---|---|---|---|---|
| D1 | Positioning | **Signal enrichment feeding Pega CDH. Not a parallel NBA engine. No email, no CRM write, no channel.** | Pega CDH is the live strategic decisioning platform across Retail, Wealth, Commercial. A second delivery path duplicates funded work and fails architecture review. | CNIM/2748099897, CDES/2722477915 |
| D2 | Integration route | **Build detectors as MIMO insights. One `user_story_id` + ODS packet + query fragment + test. ~100–200 lines each.** | MIMO is a production Spark insight factory that already publishes non-personal insights to Pega CRM for COMMERCIAL. Hive tables `cpb_parties_accounts_relations_v`, `non_personal_transactions` exist. Do not rebuild an integration path. | ~62ac69be.../2738127396 |
| D3 | Priority | **Exogenous events first. Then growth-side endogenous. Then correlation.** | Zero NBAs in the estate trigger on external events. Outflows brief answers "3rd party data required?" = No. This is the only genuine capability gap. | CDAA/2614397509, CDAA (all briefs) |
| D4 | Agent runtime | **Option B: local Strands+Ollama first, then AgentCore Runtime. Detection stays deterministic. LLM narrates verified facts only.** | AgentCore governance is 5 gated phases (AIRA, AIDEA, MMS, IMV, MCR). Do not enter with an unproven concept. Strands ships `ollama` + `llamacpp` providers — same code, swap provider. | 2799799934, 2646935395, local probe |
| D5 | FinCrime | **Read nothing. Code-level refusal on `FSA_PRD_FINCRIME`, `FSA_PRD_FC_ANALYTICS`, `PEP_PRS`, `*_XDO`.** | *Corrected from Rev 1.* The bank **does** permit FinCrime→NBA under formal least-privilege data contract. This PoC has no contract, provider, or registered purpose — so reads none. PEP+geopolitical prohibition stands separately. | DSI/2825263679 |
| D6 | ML placement | **No ranker in this codebase. Client-specific baselines yes; arbitration no.** | *Corrected from Rev 1.* RM outcome data **does** exist (response taxonomy + NBA Response file). Blocked on access, not data. Pega adaptive models own arbitration learning. | CDAA/2614397509, DAOLY/2655788833 |
| D7 | Key strategy | **`PRTY_ID` internally, map at boundary. One isolated mapping module.** | No clean FDM key path exists today. NPDM is BCDM-aligned; Pega uses Enterprise Customer ID; MIMO uses `prophet_party_id`/CIN. Pega HLDD records FDM key alignment as not aligned, future. | CDES/2722477915 |

Note on D5: this repo's `CLAUDE.md` hard boundary ("never read, load, or
reason over ground-truth labels... FinCrime governance path") is a
stricter, code-enforced version of D5's "read nothing" ruling — the
decision record's rationale (no contract/provider/purpose yet) is the
organisational justification behind the boundary already enforced here.

### Three Rev-1 corrections

1. **"No NBA capability exists" — wrong.** Pega CDH is live. Outflows NBA
   already does threshold detection on NPDM transactions, CMM scoping,
   sector exclusions, weekly batch to CRM with figures in a 200-char
   text.
2. **"No RM outcome data" — wrong.** Structured taxonomy exists: Customer
   Engaged / Not Appropriate / Remind Me Later, each with named
   sub-reasons. Plus an NBA Response file flowing Pega→CRM.
3. **"MIMO is an ML engine" — wrong.** MIMO is a Spark rules-and-query
   factory. Its relevancy function is literally `add_relevancy`,
   documented as "placeholder". Pega does decisioning; Pega adaptive
   models do learning.

### What genuinely does not exist (4 gaps)

- **Gap 1 — Exogenous events.** Nothing in CDAA, CDES or MIMO triggers an
  NBA on a market, political, regulatory or industry event. A client
  winning a tender or hit by a sanctions change produces no NBA today.
- **Gap 2 — Cross-domain correlation.** Each live NBA is one rule over
  one asset. Nothing assembles evidence across deposits, lending,
  treasury and risk into one confidence-weighted hypothesis.
- **Gap 3 — Hypothesis layer.** Live NBAs give a fact and leave reasoning
  to the RM. No system states *why* the fact implies a need, nor sizes a
  specific action.
- **Gap 4 — Growth-side signals.** Money *leaving* is well covered
  (Outflows, Leakage). Money *arriving* and facility-side signals are
  not.

These four gaps are effectively the product thesis for DataInsights: it
exists to fill exactly these holes without duplicating Pega CDH/MIMO
(per D1/D2) and without touching FinCrime data (per D5).

### Non-negotiable constraints carried forward

- **Human review before action.** Output is a reviewable recommendation.
  A person decides.
- **No autonomous multi-step agent.** Correlation is a deterministic
  rules engine. LLM only extracts and narrates, always validated, always
  with template fallback.
- **Ground truth isolated.** Labels in `protected_evaluator_only/`, three
  enforcement layers. Extends to every new detector.
- **Deterministic first.** Stats and rules before ML. Explicit formula
  before trained ranker. Every number traceable or labelled illustrative.
- **Constraint change under D4:** "local inference only, enforced by
  non-localhost validators" holds through Stage 2. It is **explicitly and
  documentedly relaxed at Stage 3** when the model provider swaps to the
  internal Model Gateway. That is a governed decision, not an accident —
  and it is the only constraint that changes.

These constraints map directly onto this repo's existing `CLAUDE.md` hard
boundaries (never read ground truth, local Ollama only, detector/
narrative/evaluator role separation, as-of correctness) — this decision
record is the enterprise-level rationale for rules already enforced here.
The Stage 3 Model Gateway relaxation is new information not currently
reflected in `CLAUDE.md`'s "No paid model or cloud calls" boundary — this
should be treated as a **future, explicitly-gated** exception, not
something to act on now.

---

## Tab 2 — Landscape & Gaps (partial)

### What exists today — end to end

Flow (as boxes on the page):

```
Source systems (27 sources)
  -> DLR / S3 raw (bronze)
  -> Snowflake RAW (COPY INTO)
  -> NPDM (cid_standardized)
  -> Nexus (integration layer)
  -> Pega Presentation (6 SFPGMOD views)
  -> S3 + manifest + .tok (interim)
  -> Pega PostgreSQL (local copy required)
  -> CDH arbitration (adaptive models)
  -> CMM CRM (200-char text)
  -> RM

MIMO (Spark insight factory)
  -> Kafka / S3 (NRT + batch)
  -> Pega CDH   <- existing endogenous signal path. COMMERCIAL franchise supported.
```

> **Missing entirely:** any path from external market / political /
> regulatory / industry events into decisioning. Also missing:
> cross-domain evidence assembly, and a hypothesis-plus-sized-action
> layer.

This matches Gaps 1-3 above and is the clearest single diagram of why
DataInsights's endogenous+exogenous correlation is additive rather than
duplicative of Pega/MIMO.

### Domain coverage — where data actually lives

Legend: 🟢 strong · 🟠 partial · 🔴 thin/absent · ⬜ out of scope

| Domain | In NPDM? | Physical home | Endogenous signals live today | Gap |
|---|---|---|---|---|
| Deposits | 🟢 strong | `cid_standardized`, `ENT_PRD.TIER0_PRS` | Outflows NBA (£5m external net-outflow, weekly, CMM). MIMO non-personal spend insights. | Cash buildup, dormancy, revenue pattern change, **large incoming payment** |
| Lending | 🟢 strong | `cid_standardized`, Loan IQ, RMP, Lombard, RBSIF | Lombard Leakage NBA, Refreshed Lending Tile, Mandatory Lending Referrals (eFlex) | Utilisation spike, maturity approaching, collateral coverage, fixed-rate expiry |
| Risk | 🟠 partial | CRADLE → Data Marketplace, 20 IRB PD models | 🔴 none — PD/LGD published as data products but nothing wires them to decisioning | Rating downgrade, PD migration, concentration risk |
| Treasury | 🔴 thin | TILAPI (bank's own BoE position), Potter (1 mention) | 🔴 none | **All of it.** No confirmed C&I client-facing treasury source found — raise before scoping |
| Economic Crime | ⬜ by design | `FSA_PRD_FINCRIME`, 4,178 tables | CBML NBA via formal data contract | out of scope — D5 |

> **Consequence:** cross-domain correlation cannot be built from NPDM
> alone. Deposits + Lending come from NPDM. Risk needs CRADLE via Data
> Marketplace. **Treasury has no identified source** — that is a real
> finding, not an assumption.

Note: "large incoming payment" appears **bold** in the Deposits gap
column on the source page — this is the one gap this repo's existing
`detection_engine/large_incoming_payment.py` already targets, so it's
worth flagging as the one row where DataInsights code and this landscape
map already connect. The Treasury "no identified source" finding is a
genuine open risk for any Domain-3/TILAPI-based signal referenced in
`docs/fdm_reference.md`'s "Five Banking Domains" section — that domain's
physical data path is not actually confirmed to exist for C&I clients.

### The live NBA we must match — Outflows

This is the existing Pega/CMM NBA that any new DataInsights signal must
be calibrated against, spec'd in full so results are comparable:

| Field | Value |
|---|---|
| Trigger | £5m+ cumulative external net-outflow, rolling 4 weeks |
| Source | `NPDM_transactions` + MIMO leakage asset |
| Segment | CMM: Regional RM (£750k–£2m), Commercial RM (£2m+) |
| Excludes | Managed Funds, Finance Cos, Leveraged Funds, Banks, Financial Services |
| Delivery | CMM CRM, weekly batch |
| CRM text | 200 char max |
| Score | Business Value = 955 |
| Volume | 50–100 customers per period |

> **Reuse three things exactly:** the 200-char limit, the Business Value
> scale, and the response taxonomy. Matching the taxonomy makes our
> results directly comparable to live NBAs — which is what eventually
> justifies or kills the capability on evidence.

> **Calibration target:** 50–100 per period. If our detectors fire on
> 3,000 of 300 clients, thresholds are wrong by an order of magnitude. An
> unworkable worklist is worse than none.

This "50-100 per period, not 3,000 of 300 clients" calibration target is
a concrete, quantified version of this repo's existing hypothesis-sizing
discipline — worth checking any new detector's threshold against before
calling it tuned.

### RM Response Taxonomy — reuse verbatim

- **Customer Engaged** (green): Funds for operational reasons · Made
  aware of deposit rates/solutions · Funds for business expansion · Will
  return funds shortly (<1m) · Has treasury function at other banks ·
  Advises better rates elsewhere · Will return some if better rate
- **Not Appropriate** (red): Reasons already known · Recent discussion
  held · Competitor better rates · No opportunity · Leaving bank ·
  Customer taking funds away
- **Remind Me Later** (amber): 1 month · 2 months · 3 months

> **This is the ML training label.** It exists, it is structured, and it
> flows back Pega→CRM. Access is the blocker, not availability — hence
> D6.

---

## Tab 3 — Architecture

### Target architecture — what we add vs. what we reuse

Legend: ⬜ exists — reuse untouched · 🟩 new — DataInsights builds · 🟥
gap being closed

**Layer 1 — Sources**

- `ENT_PRD.TIER0_PRS` (reuse) — PARTY, AGREEMENT, EVENT_FINANCIAL,
  PARTY_ASSET, COLLATERAL, PARTY_GROUP
- `cid_standardized` (reuse) — C&I NPDM, 22 data products
- CRADLE via DMP (reuse) — IRB PD/LGD, 20 models
- **EXOGENOUS SOURCES** (gap being closed) — ECB SDMX · TED · Eurostat ·
  OpenSanctions · EUR-Lex · GDELT · EM-DAT

**Layer 2 — Detection (deterministic, per domain)**

- Deposits detectors (new): `large_incoming_payment`, `cash_buildup`,
  `dormancy`, `revenue_pattern_change`
- Lending detectors (new): `utilization_spike`, `maturity_approaching`,
  `collateral_coverage_drop`
- Risk detectors (new): `rating_downgrade`, `pd_migration`,
  `concentration_risk`
- **Exogenous matcher** (gap being closed): sector + geography as-at date
  + **EXPOSURE QUALIFICATION**

**Layer 3 — Correlation (the differentiator, all new)**

- **SIGNAL BUS**: normalised Signal record — `prty_id` · `domain` ·
  `direction` · `magnitude` (0..1) · `evidence_ref`
- **HYPOTHESIS ASSEMBLER**: (1) `RISK_REVIEW` suppresses revenue
  categories, (2) multi-domain confirmation curve, (3) exogenous
  alignment, (4) decomposable strength score
- **DE-DUPLICATION**: vs C&I NBAs, Leads & Opportunities — suppress or
  enrich existing

**Layer 4 — Output & Delivery (reuse existing rails)**

- MIMO insight record (new record shape) — `user_story_id` + ODS packet +
  insight_attributes map
- MIMO platform (reuse) — Airflow + EMR + universal_driver, cadence,
  joins, publish, retry
- Pega CDH (reuse) — arbitration + adaptive models
- CMM CRM (reuse) — 200-char text
- → RM decides

> **The economy of D2:** Layer 4 is almost entirely reuse. We produce a
> record in MIMO's shape and inherit orchestration, party/account joins,
> contact cadence, publish, retry, and the test harness. Roughly 100–200
> lines per insight instead of a pipeline.

This four-layer picture is the architectural backbone this repo's
`docs/architecture.md` pipeline diagram should eventually be checked
against/reconciled with — Layers 1-2 map onto this repo's
`OfflineLocalSource`/`detection_engine/`, but Layer 3 (Signal Bus,
Hypothesis Assembler, De-duplication) and the exogenous matcher are not
yet things this repo's code implements.

### Why exposure qualification is the whole trick

```
Step 1  sector match       event NACE  -> PARTY_DEMOGRAPHIC   (as at event date)
Step 2  geography match    event region -> PARTY_LOCATOR      (as at event date)
Step 3  EXPOSURE QUALIFICATION  <- the part that matters
        Does the client actually hold exposure this event affects?
          rate change    -> floating-rate borrowing OR material idle cash?
          energy shock   -> energy-intensive sector AND thin margins?
          tender award   -> capacity to deliver? existing WC headroom?
          sanctions      -> counterparty or corridor exposure?
```

> **Without step 3 this is a mailing list.** Sector-plus-country alone
> produces a broadcast. Requiring demonstrable exposure from the client's
> own FDM data is what makes it a signal an RM can defend.

> **Use the bank's own sector data.** S&P/Trucost **Company Revenue
> Sector Split** gives revenue-weighted exposure — a client with 60%
> revenue in energy is far more exposed than one with 5%. Far better than
> a single primary NACE code. Contact: Climate Data Products squad.

### Boundaries enforced in code

- **FinCrime refusal (D5).** Reject any connection string or table
  reference resolving to `FSA_PRD_FINCRIME`, `FSA_PRD_FC_ANALYTICS`,
  `PEP_PRS`, or any `*_XDO` share. Test asserts the refusal. Docstring
  states the accurate reason: no data contract, no named provider, no
  registered purpose.
- **The one permitted read.** `HIGH_RSK_CUST_IND` on `PARTY` is
  canonical. The system may **observe** it and use it to **suppress** a
  revenue recommendation. Never rank on it. Never correlate it with an
  event type. Never surface it in RM narrative. Encode the asymmetry.
- **PEP + geopolitical — hard prohibition.** Never combine a PEP flag
  with a political or geopolitical event type. This specific combination
  is where a defensible design stops being one.
- **No individual creditworthiness inference.** Sector-level events must
  not produce an implied credit assessment of a legal entity.
- **Vulnerable / Consumer Duty.** Surface the flag, let the reviewer
  decide. Note Outflows explicitly chose to *include* vulnerable
  customers — so this is a per-NBA judgement, not a blanket rule.

These are the concrete, code-level versions of `CLAUDE.md`'s hard
boundaries — in particular "the one permitted read" (`HIGH_RSK_CUST_IND`:
observe/suppress only, never rank/correlate/narrate) is a much sharper
rule than anything currently written in this repo's `CLAUDE.md`, and
should probably be folded into it if/when this signal is implemented.
Likewise the PEP+geopolitical hard prohibition is a specific case of "no
paid model/cloud calls" plus the FinCrime boundary that isn't spelled out
today.

### FDM Join Backbone — `ENT_PRD.TIER0_PRS`

```sql
-- Client to transactions (endogenous detector backbone)
PARTY p
  JOIN PARTY_AGREEMENT pa          ON p.PRTY_ID = pa.PRTY_ID
  JOIN AGREEMENT a                 ON pa.AGRMNT_ID = a.AGRMNT_ID
  JOIN AGREEMENT_DAILY_BALANCE b   ON a.AGRMNT_ID = b.AGRMNT_ID
  JOIN EVENT_FINANCIAL e           ON a.AGRMNT_ID = e.AGRMNT_ID_TRN_ACCT
WHERE p.PRTY_SGMNT_CD IN (...)             -- C&I segments only, never retail
  AND b.EFFECTIVE_END_DT IS NULL           -- current, or as-at filter

-- As-at (point-in-time correct) pattern -- USE THIS, not current-only
WHERE EFFECTIVE_START_DT <= :as_at
  AND (EFFECTIVE_END_DT IS NULL OR EFFECTIVE_END_DT > :as_at)

-- Client to collateral coverage
PARTY -> PARTY_TO_PARTY_ASSET -> PARTY_ASSET -> ASSET_VALUE
      -> AGREEMENT_COLLATERAL_ITEM -> AGREEMENT   (coverage vs AGRMNT_ORIG_LIM)

-- Client to group hierarchy (group treasury)
PARTY -> PARTY_TO_PARTY_GROUP -> PARTY_GROUP   (traverse PRTY_GRP_PARENT_ID)

-- Sector / geography for exogenous matching -- MUST be as-at event date
PARTY -> PARTY_DEMOGRAPHIC (NACE)     PARTY -> PARTY_LOCATOR (country)
```

> **Bi-temporal is load-bearing.** Every table carries
> `EFFECTIVE_START/END_DT/TM`, `LAST_UPDATED_DT/TM`, `CRT_DT/TM`,
> `EDI_FD_ID`, `EDI_FD_RUN_ID`, `DTST_ID`, `REC_ID`. The generator must
> emit **multiple effective-dated versions** of slowly-changing rows or
> the as-at filter is never exercised and fails on first real data.
> `rating_downgrade` is the detector that proves it.

This "as-at, not current-only" pattern is the concrete SQL form of this
repo's `CLAUDE.md` "as-of correctness" hard boundary (no future leakage
into detectors/backtests) — and the bi-temporal callout is a direct,
actionable requirement for this repo's synthetic data generator: it must
emit multiple effective-dated rows per entity, not just current-state
snapshots, or as-at correctness can never actually be tested.

---

---

## Tab 4 — Output Schema

### Primary target — MIMO insight record

```
# Required columns -- every insight is a Spark DataFrame row
prophet_party_id           # ALWAYS -- map from PRTY_ID at boundary
account_id                 # if account_specific_insight = true
event_timestamp
insight_attributes         # MapType(String,String) or JSON string
insight_id
record_creation_timestamp
user_story_id              # MAX 30 CHARS -- asserted in InsightTester
+ date columns for delivery / expiry windows
```

**ODS packet config:**

```
consumer_level_1      = "PEGA"
consumer_level_2      = "COMMERCIAL"
consumer_level_3      = "CRM"
pega_franchise        = "COMMERCIAL"
account_specific_insight = true | false
insight_attributes    = { ...template of keys emitted... }
spending_dict.copy    = "copy template with {tokens}"
requires_budgets      = false
cicd.version          = "..."
```

> **Two hard constraints:** `user_story_id` max 30 chars, and every
> attribute key must exist in the Attributes Catalogue (page 2863562828)
> before emission. Keys are `snake_case` in Python, converted to
> `dot.case` on the wire by the driver.

### Secondary target — Pega presentation layer

| Table | View |
|---|---|
| SFPGMOD001_DS_PEGA_ORGANISATION | ...ORGANISATION_VW |
| SFPGMOD002_DS_PEGA_PERSON | ...PERSON_VW |
| SFPGMOD003_DS_PEGA_ACCOUNT | ...ACCOUNT_VW |
| **SFPGMOD004_DS_PEGA_ORGANISATION_EVENT** | **← OUR TARGET** |
| SFPGMOD005_DS_PEGA_PERSON_EVENT | ...PERSON_EVENT_VW |
| SFPGMOD006_DS_PEGA_ACCOUNT_EVENT | ...ACCOUNT_EVENT_VW |

> An NBA recommendation **is an event about an organisation**.
> `SFPGMOD004` is the natural insertion point if the MIMO route is not
> taken. Batch path: Snowflake COPY INTO → S3 staging → manifest + `.tok`
> → publish bucket → Pega import.

> **Pega requires a local copy** in its PostgreSQL for performance. It
> will not query Snowflake live at decision time. So output must land as
> data Pega has already ingested — you cannot expose an API and expect
> CDH to call it mid-decision.

### Attribute keys to register — `insight_attributes` map

| Key | Type | Example | Purpose | Status |
|---|---|---|---|---|
| `nba_category` | string | `FINANCING_NEED` | Maps to Pega Product Category | align |
| `crm_text` | string ≤200 | "Received £7.9m credit, 8x normal. Tender award in sector." | What the RM reads. **Hard 200-char limit** | align |
| `hypothesis` | string | "Winning a tender creates a cash-flow gap between delivery and payment..." | **The reasoning layer** | NEW |
| `recommended_action` | string | "Offer WC financing ~£1,973,422 (25% of tender value, illustrative)" | **Sized action** | NEW |
| `business_value_score` | int | 955 | Pega arbitration. **Match existing scale** | align |
| `confirming_domains` | string | `deposits,lending` | **Cross-domain confirmation** | NEW |
| `signal_strength` | int 1–5 | 4 | Decomposable confidence | NEW |
| `endogenous_signal_type` | string | `large_incoming_payment` | Which detector fired | NEW |
| `exogenous_event_type` | string | `public_tender_award` | **The capability gap** | NEW ★ |
| `exogenous_event_source` | string | `TED` | Provenance — required for governance | NEW ★ |
| `exogenous_event_date` | date `dd/mm/yyyy` | 14/08/2026 | Timing of external event | NEW ★ |
| `evidence_ref` | string | `EVENT_FINANCIAL:EVNT_ID=...:eff=...` | Full traceability to source row | NEW |
| `sizing_basis` | string | `25pct_of_event_value_illustrative` | Discloses the heuristic inline | NEW |
| `response_actions` | fixed taxonomy | Customer Engaged / Not Appropriate / Remind Me Later | Reuse live taxonomy verbatim | align |

Keys marked ★ are the exogenous-specific ones — the load-bearing new
surface area this whole PoC adds. `sizing_basis` making the heuristic
explicit inline (e.g. `25pct_of_event_value_illustrative`) is the field
that operationalizes this repo's "never claim a raw score, always
disclose the sizing method" hypothesis-outcome discipline
(`hypothesis_outcome_format` memory) at the schema level.

> **Platform type conventions:** dates as `dd/mm/yyyy`, amounts as int or
> 2-dp float, flags as `1/0` or `True/False`. Check
> `parser_config.deprecated_attributes` before naming anything new —
> deprecated names pass tests but build debt.

### Key mapping — isolate in one module

| Our internal key | MIMO boundary | Pega boundary |
|---|---|---|
| `PRTY_ID` — FDM common key. `ENT_PRD.TIER0_PRS`. The direction the estate is heading. | `prophet_party_id` / CIN — what the insight record requires. | Enterprise Customer ID — Interaction History key today. |

> **There is no clean FDM key path to Pega today.** NPDM is
> BCDM-aligned. Pega's own HLDD records both "semantics aligned to FDM"
> and "uses FDM kernel common keys" as **N — to be revised in future**.
> So: key on `PRTY_ID` internally, map at the boundary, and put that
> mapping in **one module** so it becomes a no-op when the FDM conversion
> lands. Do not spread it through detector code.

This directly operationalizes D7 from Tab 1 and is a concrete engineering
instruction: any DataInsights code should use `PRTY_ID` everywhere
internally and isolate all `prophet_party_id`/Enterprise-Customer-ID
translation behind one boundary module.

---

## Tab 5 — Phasing

**Ordering rule:** what is *missing* from the current estate is built
first. What already exists is reused, not rebuilt. Every phase extends
the previous one and ends at a gate that must pass before the next
begins.

| Phase | Name | Description | Gate |
|---|---|---|---|
| 0 | Validate route and demand — no code | Three conversations: MIMO AIEngine (Rohit Jain) on whether a COMMERCIAL insight can carry exogenous attributes; C&I Decisioning on whether an exogenous NBA is wanted, ideally with a product owner who wants a specific one; Climate Data Products on Company Revenue Sector Split access; confirm the D4 AgentCore staging decision. | A named stakeholder in C&I Decisioning who agrees the gap is worth closing. Without this the rest is an unrequested science project. **This gate matters more than any technical one.** |
| 1 | One exogenous NBA as a MIMO insight — closes Gap 1 | Single clearest case: **public tender award (TED) + matching large incoming payment**. Built as a MIMO insight — one `user_story_id`, one ODS packet, one query fragment, one test. Register new attributes in the Attributes Catalogue first. Local Strands + Ollama for narrative. Tags: highest value · ~100–200 lines · existing production route. | A synthetic client with genuine sector exposure matches the tender award and produces a sized, hypothesis-carrying recommendation. A client in the same sector **without** exposure does **not** match. *The negative case is the real test.* |
| 2 | FDM schema migration — no new detectors | Reshape generator to FDM entities. Bi-temporal audit columns with **multiple effective-dated versions**. `code_domains.py` with disclosed invented sets, using real segment codes where documented. Port `large_incoming_payment` to read `EVENT_FINANCIAL`. Extend `DataSource` to FDM shape. Deliberately *after* Phase 1 so it is informed by a real integration rather than an assumed one. | All 16 existing tests pass against FDM-shaped data. Schema conformance, FK integrity and temporal invariant tests pass. **Zero new detectors** — this phase proves the migration is neutral. |
| 3 | Growth-side endogenous detectors — closes Gap 4 | `cash_buildup`, `dormancy`, `revenue_pattern_change`, `facility_utilization_spike`, `facility_maturity_approaching`, `collateral_coverage_drop`, `fixed_rate_expiry`. All on data NPDM already holds. Do not build a net-outflow detector — Outflows NBA covers it live with MIMO leakage enrichment we lack. | Tests per detector. Path-refusal test extended to new labels. Per-detector dev-diagnostic metrics **labelled contaminated**. Volume sanity-checked against the 50–100 per period benchmark. |
| 4 | Risk domain wiring — connects existing data products | CRADLE PD/LGD via Data Marketplace. `rating_downgrade` from effective-dated history. `concentration_risk` from `PARTY_RELATED`. Notable: these data products **already exist** and nobody has connected them to decisioning. Encode risk-grade scale direction explicitly — a PD improving is a growth signal, easy to invert. | `rating_downgrade` demonstrably reads history, not current state. That is the real test of Phase 2's bi-temporal work. |
| 5 | Cross-domain correlation — closes Gaps 2 and 3 | Signal bus with normalised `Signal` record. Hypothesis assembler with the four composition rules. Category precedence (`RISK_REVIEW` suppresses revenue). Conflict recording. De-duplication against C&I NBAs, Leads & Opportunities. Needs several detectors live before there is anything to correlate — hence position 5, not earlier. | Three fixtures: (a) multi-domain client produces one correlated recommendation with decomposable strength; (b) risk signal present proves suppression works and is recorded; (c) existing open NBA proves de-duplication. |
| 6 | Treasury — scope only after a source is identified | `fx_exposure_building`, `group_cash_pooling` (traverse `PRTY_GRP_PARENT_ID`). Attribute group recommendations to the group treasurer relationship, not one subsidiary. **Blocked**: no confirmed C&I client-facing treasury source found. TILAPI is the bank's own BoE position, not client data — **do not build a detector against it.** | A named client-facing treasury data source exists. Until then this phase stays unscoped. |
| 7 | SME validation — gating, not additive | Code value domains reviewed and corrected. Risk-grade scale and direction confirmed by Wholesale Credit Risk. Cardinality and distributions reviewed. Thresholds calibrated against volume benchmark. Category mappings reviewed by product owners. | Every `SOURCE = "INVENTED"` marker either replaced with a confirmed value set or explicitly accepted as a documented approximation. |

Phase 6's explicit "blocked" status directly confirms the Tab 2 finding
that Treasury/TILAPI has no identified C&I client-facing source — this
is now stated twice, independently, as a reason not to build Domain 3
detectors yet. Phase 7 (SME validation) is the final phase — 8 total
(0-7), matching the intro's "eight numbered phase cards."

### AgentCore staging — D4 in practice

```
STAGE 1 -- Local
Strands + OllamaModel -> localhost:11434 - deterministic detectors - FDM synthetic data - sklearn baselines. Zero governance overhead.
   |
   v
STAGE 2 -- Prove one NBA locally
End-to-end, shaped as a MIMO insight record. Still all local.
   |
   v
STAGE 3 -- AgentCore
Swap OllamaModel -> internal Model Gateway. Containerise. Enter governance with a working artefact and evidence.
```

```python
def get_model(cfg):
    if cfg.mode == "local":
        from strands.models.ollama import OllamaModel
        return OllamaModel(host="http://localhost:11434",
                            model_id=cfg.model_id)
    from strands.models.bedrock import BedrockModel
    return BedrockModel(model_id=cfg.model_id)
```

> Put the provider behind this factory **from day one**. Existing
> non-localhost validators stay active in local mode and are relaxed at
> Stage 3 as a documented decision.

This confirms the earlier note about the Stage 3 Model Gateway relaxation
being a **future, explicitly-gated** exception — the code pattern is a
swappable factory function, with local-only validators enforced until
Stage 3 is reached deliberately, not by omission. Note the sample code
names `BedrockModel` as the Stage 3 provider — this is illustrative of
the swap mechanism, not a statement that this repo may call Bedrock now;
`CLAUDE.md`'s "No paid model or cloud calls" and "AWS/Bedrock/AgentCore
adapters are contract-and-mock only until explicitly authorized" still
govern current behaviour.

### AgentCore governance — why Stage 3 is last

| Phase | Requirements | Gate |
|---|---|---|
| 1 Pre-dev | AIRA Front Door submission, AIRA Tier 1–4 classification, Model Risk Owner + Model Owner assigned, Inherent Risk Questionnaire, DRA, Security STaRT, PIA | Cannot start dev without AIRA classification + MRO/MO |
| 2 Dev | AIDEA assessment, fairness/bias approach, explainability, monitoring thresholds, data lineage, **human-in-the-loop**, audit trail, **feedback capture** | AIDEA approval before UAT |
| 3 Model gov | MMS registration, Model Tiering, Model Development Documentation, **Independent Model Validation (Tier 2–4)**, KPIs, drift thresholds, retraining cadence | Model Owner go-live approval |
| 4 Pre-prod | MCR, Change Risk Review, STaRT closure, DRA closure, PIA closure, UAT, SRE Production Readiness, rollback plan, CAB | MCR authorised + all closures |
| 5 Post-prod | Monitoring dashboards, feedback capture live, 30-day Model Performance Review, Model Risk Committee reporting, fairness metrics, quarterly AIDEA review | ongoing |

> **Two things already in our favour:** the checklist requires
> *human-in-the-loop* and *feedback capture* — our design has both by
> construction. Deterministic detection also keeps us low on the tiering
> versus an autonomous agent.

This 5-phase AgentCore governance gate is the concrete backing for D4's
"do not enter with an unproven concept" rationale from Tab 1, and gives a
literal checklist for what "Stage 3" (from the staging diagram above)
actually requires before this PoC could touch a governed model gateway.

---

## Tab 6 — Extension Slots

**Design intent:** every layer has a named, typed insertion point. Adding
a domain, a table, an event source or a data product should mean filling
a slot — never restructuring the pipeline. Slots are drawn as dashed
boxes: defined contract, not yet populated.

### Slot Type A — new source domain behind `DataSource`

```python
class DataSource(Protocol):            # exists -- do not change shape
    def party(self, as_at)                  -> DataFrame
    def arrangement(self, as_at)             -> DataFrame
    def daily_balance(self, as_at)           -> DataFrame
    def financial_event(self, window)        -> DataFrame
    def party_asset(self, as_at)             -> DataFrame
    def party_group(self, as_at)             -> DataFrame
    def party_demographic(self, as_at)       -> DataFrame  # NACE, as-at
    def party_locator(self, as_at)           -> DataFrame  # country, as-at
    # ---- SLOTS ----
    def risk_measure(self, as_at)            -> DataFrame  # SLOT A1: CRADLE PD/LGD
    def treasury_position(self, as_at)       -> DataFrame  # SLOT A2: source TBC
    def collateral_item(self, as_at)         -> DataFrame  # SLOT A3: COLLATERAL_ITEM

# Implementations
OfflineLocalSource(DuckDB/CSV)      # exists, used today
SnowflakeSource(cid_standardized)   # written, never executed
EntPrdSource(ENT_PRD.TIER0_PRS)     # SLOT A4: FDM-native reader
DataMarketplaceSource(CRADLE)       # SLOT A5: risk data products
```

| Slot | Description |
|---|---|
| A1 — Risk measures | CRADLE via Data Marketplace. 20 IRB PD models (`LC_MODEL_DATA`, `ML_MODEL_DATA`, ...) + facility-level LGD. **Products exist, unwired.** Fills at Phase 4. |
| A2 — Treasury | 🔴 **source unknown**. TILAPI is the bank's own BoE position, not client data. Needs a named C&I client-facing source before Phase 6 can be scoped. |
| A3 — Collateral | `COLLATERAL_ITEM`, `COLLATERAL_ITEM_VALUE`, `AGREEMENT_COLLATERAL_ITEM`. Sources CMS, GMS, MMR/MMU. Fills at Phase 3. |

This confirms `OfflineLocalSource` and a written-but-never-executed
`SnowflakeSource(cid_standardized)` already exist in this repo's
codebase — worth checking against `docs/architecture.md` and
`docs/current_state.md` to see if those names match what's actually
there, since this decision record may be describing target/aspirational
naming rather than the current file layout.

### Slot Type B — new detector on the signal bus

```python
@dataclass(frozen=True)
class Signal:                    # STABLE CONTRACT -- every detector emits this
    prty_id:        str          # correlation key
    signal_type:    str          # 'cash_buildup', 'rating_downgrade', ...
    domain:         str          # deposits|lending|treasury|risk|exogenous
    direction:      str          # increase|decrease|neutral
    magnitude:      float        # normalised 0..1, comparable across domains
    observed_date:  date
    evidence_ref:   str          # table + key + effective date
    source_tables:  list[str]
    raw_measure:    dict         # domain-native value, kept for narrative

class Detector(Protocol):
    domain: str
    signal_type: str
    def detect(self, src: DataSource, as_at: date) -> list[Signal]: ...
```

| Domain | Live now | Slotted — phase | Reads |
|---|---|---|---|
| Deposits | `large_incoming_payment` | `cash_buildup` (P3) · `dormancy` (P3) · `revenue_pattern_change` (P3) | EVENT_FINANCIAL, DAILY_BALANCE |
| Lending | — | `utilization_spike` (P3) · `maturity_approaching` (P3) · `collateral_coverage_drop` (P3) · `fixed_rate_expiry` (P3) | AGREEMENT, COLLATERAL_* |
| Risk | — | `rating_downgrade` (P4) · `pd_migration` (P4) · `concentration_risk` (P4) | PARTY.RSK_GRD_*, PARTY_METRIC, CRADLE |
| Treasury | — | `fx_exposure_building` (P6) · `group_cash_pooling` (P6) | PARTY_GROUP + Slot A2 |
| Exogenous | — | `external_event_match` (P1 ★) | PARTY_DEMOGRAPHIC, PARTY_LOCATOR |

> Slot B-open: any future domain implements `Detector` and appears on the
> bus with no assembler change.

The `Signal` dataclass here is the concrete schema for what Tab 3's
"SIGNAL BUS" architecture box (normalised Signal record — `prty_id` ·
`domain` · `direction` · `magnitude` 0..1 · `evidence_ref`) actually
contains, and the phase tags in this table (P1★, P3, P4, P6) are the
authoritative cross-reference for which detector belongs to which Tab 5
phase — e.g. `large_incoming_payment` is confirmed "live now," matching
this repo's existing `detection_engine/large_incoming_payment.py`.

### Slot Type C — new exogenous event source

```python
class ExternalEventSource(Protocol):
    def fetch(self, window) -> list[ExternalEvent]

@dataclass
class ExternalEvent:
    event_type:      str
    event_date:      date
    sector_codes:    list[str]   # NACE
    country_codes:   list[str]
    direction:       str
    severity:        float       # 0..1
    estimated_value: float|None  # TED, EM-DAT publish this
    source:          str         # provenance -- REQUIRED for governance
    source_ref:      str
```

| Event type | Real source | Extraction | Slot |
|---|---|---|---|
| Tender award | TED API | none — has value | **P1 ★** |
| Rate policy | ECB SDMX | none | open |
| Energy shock | Eurostat / ECB | none | open |
| Sanctions | EU list / OpenSanctions | none | open |
| Regulatory | EUR-Lex | sector interpretation | open |
| Natural disaster | EM-DAT | none — has damage est. | open |
| Geopolitical | GDELT | **LLM extraction** | last |

> **SLOT C-SECTOR — exposure weighting.** S&P/Trucost **Company Revenue
> Sector Split.** Revenue-weighted sector exposure replaces single
> primary NACE. Materially better matching. Contact: Climate Data
> Products squad.

Only Tender award (via TED) is slotted for Phase 1; the other 5 real
exogenous sources (ECB SDMX, Eurostat/ECB, OpenSanctions, EUR-Lex,
EM-DAT) are explicitly "open" — future work, not yet built — and
Geopolitical (GDELT) is called out as needing LLM extraction and coming
last, presumably because it's the hardest to qualify safely given the
PEP+geopolitical hard prohibition from Tab 3.

### Slot Type D — output channel

```python
class OutputSink(Protocol):
    def emit(self, recs: list[Recommendation]) -> None

MimoInsightSink()        # SLOT D1 -- PRIMARY, Phase 1
PegaEventSink()          # SLOT D2 -- SFPGMOD004_ORGANISATION_EVENT
WorklistCsvSink()        # exists -- DEMO ONLY, label as such
StreamlitSink()          # exists -- DEMO ONLY, label as such
DataProductSink()        # SLOT D3 -- publish via DPPS to DMP
```

> **SLOT D3 — governed data product.** If DataInsights output is ever
> published as a governed product: Data Product Scoping Canvas, naming
> standard, HLDD mapping to FDM classes (anchor on **EVENT**), ServiceNow
> SNSVC number, DPPS metadata upload, Kitemark score.

> **Never a slot: arbitration or final ranking, email, SMS, direct mail,
> CRM write, or any outbound channel.** Pega owns delivery. This is a
> hard architectural boundary, not a backlog item.

This confirms `WorklistCsvSink()` and `StreamlitSink()` already exist in
this repo (matching `docs/user_guide.md`'s digest/worklist outputs) and
are explicitly labelled **demo only** — i.e. not the production delivery
path, which is MIMO insight → Pega CDH (D1/D2) per D1 from Tab 1.

### Slot Type E — ML challengers, behind explicit interfaces

```python
class MagnitudeNormaliser(Protocol):   # SLOT E1
    def normalise(self, raw, ctx) -> float     # explicit fn now; learnable later

class BaselineModel(Protocol):         # SLOT E2  <- highest ML value
    def expected(self, prty_id, metric, as_at) -> tuple[float,float]
    # client-specific normal, replaces flat thresholds like Outflows' fixed £5m

class ExposureQualifier(Protocol):     # SLOT E3
    def qualifies(self, prty_id, event) -> tuple[bool,float]

class PropensityModel(Protocol):       # SLOT E4
    def score(self, prty_id, category) -> float   # label = RM response taxonomy
```

| Slot | Note |
|---|---|
| E1 Normaliser | Explicit documented function first. Auditable baseline stays as the challenger benchmark. |
| E2 Baselines ★ | **Best ML value.** £5m from a £2m-turnover client vs a £500m one are different events. Turns a threshold into an anomaly score. |
| E3 Exposure | Supervised once labels exist. Trucost revenue split + facility structure + transaction pattern. |
| E4 Propensity | Close to what Pega adaptive models do. **Coordinate, do not compete.** |

> **Not a slot: arbitration or final ranking.** Pega adaptive models own
> that and already learn from responses across all NBAs. A competing
> ranker fragments the learning signal and creates two systems
> disagreeing about priority.

> **Local ML runway:** Phase 1–3 baselines run on this machine with
> scikit-learn on small data. Production training uses the existing
> rails — Airflow model-input DAG → SNOWDQ → S3 → Watchtower →
> Kepler/SageMaker → model-output DAG → Snowflake external tables. That
> pattern is documented and in service.

E4's "coordinate, do not compete" restates D6 from Tab 1 (no ranker in
this codebase) at the ML-interface level — client-specific baselines
(E1/E2) are in scope, arbitration/ranking is explicitly not, no matter
how it's implemented.

---

## Tab 7 — Local Env

**Local Environment — Verified By Actual Install, Not Assumed.** Every
row below was confirmed by installing into a throwaway venv from the bank
Artifactory index and importing the package. The venv was then deleted.
**Nothing came from public PyPI.**

Index: `https://artifactory-server.app.banksvcs.net/artifactory/api/pypi/engx-pypi-virtual/simple`

### Agent Framework — available

| Package | Version | Status |
|---|---|---|
| `strands-agents` | 1.55.1 | installed + imported |
| `strands-agents-tools` | 0.8.8 | available |
| `bedrock-agentcore` | 1.23.0 | available |
| `bedrock-agentcore-starter-toolkit` | 0.3.12 | available |

### Data & ML Stack — available

| Package | Version | Status |
|---|---|---|
| `duckdb` | 1.5.5 | imported |
| `pandas` | 3.0.5 | imported |
| `scikit-learn` | 1.9.1 | imported |
| `numpy` | 2.5.3 | imported |
| `scipy` / `pydantic` | current | imported |
| `ollama` (py client) | 0.6.2 | imported |
| `snowflake-connector-python` | — | available |
| `streamlit` / `mcp` / `pyarrow` | — | available |

> **Expect one transient failure.** `WinError 32` file lock on `pywin32`
> (`win32com\test\testAccess.py`) mid-install — almost certainly an AV
> scanner. Just re-run pip. Do not debug it.

### The finding that makes D4 work

```
Strands built-in model providers:
  ollama     <- LOCAL
  llamacpp   <- LOCAL
  litellm    <- can route local
  bedrock, sagemaker, anthropic, openai,
  gemini, mistral, writer, llamaapi
```

> **`ollama` and `llamacpp` are first-class providers in the box.** Same
> agent code runs local or on AgentCore — swap the provider only. No shim
> layer needed.

> Also aligns with the bank's proven AgentCore pattern: LangGraph agent
> in a container behind AgentCore Runtime calling the internal Model
> Gateway. Substitute Strands for LangGraph — same shape. AgentCore is
> framework-agnostic and lists Strands explicitly.

This is the verification behind the "Local-first: Strands + Ollama
VERIFIED" badge on the page header, and directly justifies D4's chosen
architecture: it's not a hopeful assumption, it's a package-level fact
confirmed by actual install against the bank's own Artifactory.

### Two real constraints

1. **Ollama runtime is NOT installed.** Not on PATH. No `~/.ollama`. No
   `%LOCALAPPDATA%\Programs\Ollama`. The Python client is an HTTP wrapper
   for `localhost:11434` — it needs the server binary. **This is a
   software-request conversation, and it gates all local narrative
   work.** Raise now.
2. **Machine has 2 logical cores, no GPU.**
   - CPU: Intel Xeon Sapphire Rapids
   - Cores: 2 logical
   - RAM: 31.9 GB — comfortable
   - GPU: none detected

### Local workload feasibility

| Workload | Viable | Note |
|---|---|---|
| Synthetic data gen (300 clients, ~300K rows) | ✅ yes | comfortable |
| Detector logic, DuckDB queries | ✅ yes | I/O-shaped, DuckDB efficient |
| sklearn on small data (thousands of rows) | ✅ yes | logistic regression, GBM, isolation forest all fine |
| Small LLM 3B–7B quantised (Phi-3-mini, Llama 3.2 3B Q4) | 🟠 slow | tens of seconds per narrative on 2 cores |
| Batch narrative over thousands of rows | ❌ no | sample a handful, template the rest |
| Any model 13B+ | ❌ no | don't |

> **The existing design already handles this correctly.** Narrative is
> sampled not per-row, with deterministic template fallback. That
> decision looks better on 2 cores than it did on paper.

> **Ask:** if a higher-spec dev box or a Kepler/SMUS workspace is
> available for LLM work, worth requesting. **Not a blocker for Phases
> 1–3.**

This machine-spec table is this repo's existing narrative
sample-not-per-row + template-fallback design (`docs/architecture.md`,
`datainsights/narrative/`) getting an unplanned but favourable
retrospective justification — worth noting as validated, not something
to change.

### Stage 1 setup — copy/paste

```bash
python -m venv .venv
.\.venv\Scripts\activate

# resolves entirely from bank Artifactory -- retry once if WinError 32
pip install strands-agents strands-agents-tools ollama
pip install duckdb pandas scikit-learn numpy scipy pydantic
pip install pytest streamlit

# Ollama runtime must be provisioned separately -- NOT pip installable
# then:  ollama pull phi3:mini       (or llama3.2:3b)

# verify
python -c "import strands; from strands.models.ollama import OllamaModel; print('ready')"
```

> **Requirements pinning:** pin exact versions per project convention.
> Artifactory holds full version history for every package checked, so
> pinning is safe.

This setup script targets the bank's Windows/Artifactory machine (note
`.\.venv\Scripts\activate` and Windows env var paths above) — it will
need adapting for a macOS dev machine (`source .venv/bin/activate`, and
Ollama itself installs differently on macOS, e.g. via the native app or
`brew install ollama`, rather than being "not pip installable" in the
same way). This distinction matters directly for the local-Mac →
NatWest-VDI implementation plan requested separately.

---

## Page now fully captured

All 7 tabs (Decisions, Landscape & Gaps, Architecture, Output Schema,
Phasing, Extension Slots, Local Env) have now been captured from the
screenshots shared across this conversation. This document should be
treated as the authoritative condensed reference for `DECISION_RECORD.html`
going forward — see `docs/PROJECT_CONTEXT.md` for how it should be
folded into the handoff file, and `docs/fdm_reference.md` for the
companion FDM/schema reference this record repeatedly cross-references
(`ENT_PRD.TIER0_PRS`, NPDM, the 8 kernel classes, the 5 banking domains).
