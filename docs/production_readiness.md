# Production readiness: what it takes to run this as an application

**Framing.** Treating this as a product rather than a demo viewer is a
legitimate goal, and this document is the path to it. But the goal does not
change the current state: the gaps below are real, and calling the app a
product does not close them. What follows is what closing them actually
takes.

I was too pessimistic in an earlier assessment. Having read the code
properly, **more of the groundwork exists than I credited** — the
architecture anticipated this. Corrections are marked.

---

## What already exists (and I understated)

| Capability | Where | State |
|---|---|---|
| **Server-side entitlement** | `datainsights/identity.py` | Real. `Principal(user_id, role, rm_ids)`; the worklist is scoped *before* any UI filter; deny-by-default for an RM with no codes. There is deliberately no "become someone else" control |
| **Concurrent-run safety** | `datainsights/runs.py::RunLock` | Real. Honours `monitor.max_concurrent_runs`, refuses overlapping runs, and marks a run whose process is dead as `abandoned` rather than leaving a stuck lock |
| **Per-profile state paths** | `datainsights/runtime.py` | Real. `output.path` and `state.path` come from the profile and accept absolute paths — per-tenant isolation is a config change, not a rewrite |
| **Source discovery** | `dashboard/common.py` | Real as of this branch. A new profile appears with working tabs, no code edit |
| **Deterministic, auditable output** | throughout | Real, and the genuinely valuable part. Every recommendation traces to data + a rule a human can read |

So the shape is right. What is missing is the *provider* at each seam, not
the seam itself.

---

## The five real gaps, and what each actually needs

### 1. Identity is a stub — but the seam is clean

**Now.** `identity.provider: local_dev` reads `DATAINSIGHTS_USER` /
`_ROLE` / `_RM_IDS` env vars. Anyone can be anyone. `provider: idp` exists
and raises `IdentityProviderNotRun`.

**Correction to my earlier claim.** I called this "identity is fake", which
described the provider but misrepresented the design. The *entitlement*
half is genuinely built and enforced server-side; only the identity source
is a stub.

**What closes it.** Streamlit 1.63 ships `st.login()` / `st.user` — real
OIDC against Google, Microsoft, Okta or Auth0, with a signed identity
cookie and verified claims. That maps directly onto the existing seam:

```python
# dashboard/app.py, before any page renders
if not st.user.is_logged_in:
    st.login()
    st.stop()
```

Then `resolve_principal` grows an `oidc` provider that reads
`st.user.email` and maps it to a role and RM codes. The mapping has to
live somewhere real (a table, or the IdP's group claims) — that is the
actual work, not the login button.

**Size: days, not weeks.** Needs the `auth` extra, an `[auth]` secrets
block, and a registered OIDC client.

---

### 2. Single process over local files

**Now.** Streamlit serves one process; the source reads local CSVs through
DuckDB. Two users means two laptops.

**What closes it.** Two separable things, often conflated:

- **Serving** several users from one deployment: Streamlit handles
  concurrent sessions already. The blocker is not the framework — it is
  that every session shares the same on-disk state (gap 3).
- **Data at scale**: `SqlSource` exists and is conformance-tested through
  DuckDB, so pointing at Snowflake/Postgres is a profile change. But
  **Snowflake has never executed a query from this repo** — until it does,
  that is a contract, not a capability.

**Size: weeks.** And it needs a real credential path (`st.secrets`),
which does not exist yet.

---

### 3. Shared mutable state — the one I hit today

**Now.** `var/insights/` and `var/agent_traces.db` are local files, and
the whole-book generator always writes `fdm_rm_worklist.csv`. Running the
legacy profile silently overwrote FDM's worklist — I did this by accident
during this session, which is the best evidence that it is a real problem
rather than a theoretical one. The dashboard now snapshots per profile,
but the underlying collision is untouched.

**Correction.** `RunLock` already prevents two runs *colliding*; what it
does not do is give two *users* separate output.

**What closes it.**
- Output paths keyed by tenant/user, not just profile — the profile
  already supports absolute paths, so this is mostly convention plus a
  path helper.
- `agent_traces.db` moves from SQLite-on-a-laptop to a real database.
  SQLite under concurrent writers is the classic way to lose data.

**Size: days for paths; weeks for the database move.**

---

### 4. Onboarding writes into the repo

**Now.** `onboarding/accept.py` writes `config/bindings/<name>.yaml`,
`config/entities_<name>.yaml` and `config/profiles/<name>_local.yaml`
into the working tree. Fine for one engineer. For N users it means every
new dataset is a git commit, and one user's schema is visible to all.

**What closes it.** A config store outside the repo — per-tenant prefix in
a database or object store — with `load_binding` / `active_profile` reading
through it. The discovery layer I built this session already reads a
*directory*; pointing it at a tenant-scoped store is the natural extension.

**Size: weeks.** This is the one that most changes the shape of the app.

---

### 5. No deployment path has executed

**Now.** Per `CLAUDE.md` and `docs/current_state.md`: Snowflake has never
run a query; AWS/Bedrock/AgentCore adapters are contract-and-mock only
until explicitly authorized.

**What closes it.** Authorization first, then actually running it. This is
not primarily an engineering gap — the adapters exist — it is a
permissions-and-verification gap. Nothing should claim a cloud run works
until it has run.

**Size: unknown until authorized.**

---

## Honest sequencing

If the goal is genuinely "users trigger functionality in an application":

| Phase | What | Why first |
|---|---|---|
| **1** | `st.login()` + OIDC → `Principal` | Everything else is unsafe without real identity. Also the cheapest real win |
| **2** | Per-user output paths; traces to a real DB | Stops users corrupting each other's state |
| **3** | Tenant-scoped config store | Onboarding stops being a git operation |
| **4** | A real backend actually executed (Snowflake or Postgres) | Removes the local-CSV ceiling — and must be *run*, not declared |
| **5** | UI work (`docs/ui_improvement_proposal.md`) | Genuinely valuable, but polishing a single-user app does not make it multi-user |

Phase 1 is days. Phases 2–4 are weeks each. That is a project, and it
should be resourced as one — but it is an ordinary project, not a rewrite,
because the seams are already in the right places.

---

## What must not be lost

Whatever gets built, these are the properties that make this system worth
deploying at all, and each is currently enforced by tests:

- **No recommendation without traceable evidence.** Every one maps to data
  plus a rule a human can read.
- **Ground truth stays out of detection.** Enforced at three layers —
  directory, contract, and code.
- **Machine-proposed signals stay in shadow** until a human promotes them.
- **Disclosure over flattery.** Figures marked illustrative; unrun things
  marked NOT RUN.

A production build that quietly drops these would be worse than the demo,
not better — it would look authoritative while being less accountable.

---

## The one thing I would push back on

Calling it a full-fledged application is the right *ambition*. But the
sidebar disclosure ("a demo viewer, not the review/tracking system") should
change when the gaps close, not before. Users who are told this is
production will reasonably assume their login means something, their data
is theirs, and their runs are isolated. Today none of those hold.

Ship the label with the capability, not ahead of it.
