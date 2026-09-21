# UI improvement proposal

**Status: proposal only. Nothing built.** Agree the direction, then I implement.

Four problems, in the order I'd fix them. Each has a measurement, a
proposed change, and the risk of doing it. I cannot see the rendered page,
so the risk column is honest rather than reassuring.

---

## The measurements

Taken from the current code, not impressions:

| Measure | Value |
|---|---|
| `explore.py` | 764 lines — more than half the dashboard's 1,370 |
| Prose calls on that one page | 88 (`st.caption` / `st.markdown` / `st.info`) |
| Prose characters on that one page | **11,504** (~2,000 words) |
| Longest single caption | 537 characters |
| Sidebar sections | 10, plus 3 sub-tabs inside Explore |
| Theme config | **none** — `.streamlit/config.toml` does not exist |
| Worklist table columns | 12, in a 380px scroll box |

11,504 characters is a document. The page reads as documentation that
happens to contain controls.

---

## P1 — No theme (root cause of "looks unstyled")

**Problem.** There is no `.streamlit/config.toml`, so the app renders in
stock Streamlit defaults: no type scale, no brand colour, default radius,
default font. Everything else I could do to "polish" it fights this.

**Proposed.** Add `.streamlit/config.toml` from the skill's
`financial-dashboard` template — a conservative palette built for exactly
this kind of app. It sets Inter as the type face, a real heading scale
(28/22/18/16px), semantic colours (green/red/amber for positive, negative,
warning) and `dataframeHeaderBackgroundColor` so tables stop looking like
raw output.

Per the skill's guidance, this is native theming in config — **not
injected CSS**, which targets internal class names and breaks on upgrade.

The template is dark. A light variant exists (`minimal`, `solarized-light`)
if you'd rather; say which and I'll use it.

**Risk: low.** One new file, no code change, instantly reversible by
deleting it.
**Impact: high.** This alone changes the whole feel.

---

## P2 — Worklist is hard to scan (the daily view)

**Problem.** The RM's actual job is: scan a list → pick a client → read the
card. Today that is a 12-column table, then a divider, then a *separate
dropdown* to re-select the row you were just looking at, then the card
below the fold. You select twice and scroll to read.

```
  [12-column table, 380px scroll]
  ─────────── divider ───────────
  Prepare for the client call
  [Recommendation ▾]   ← re-selecting what you just clicked
  Why now: …
  Hypothesis: …
```

**Proposed.** Click the row. Streamlit 1.63 supports
`st.dataframe(..., on_select="rerun", selection_mode="single-row")` —
verified available in this repo's version.

```
  [metrics row]
  ┌ table, 6 visible cols ─────┬ client card ────────┐
  │ ▸ CL00137  FINANCING  ★★★  │  CL00137            │
  │   CL00240  TREASURY   ★    │  SME · Manufacturing │
  │   CL00292  TREASURY   ★    │  Why now: …          │
  └────────────────────────────┴──────────────────────┘
```

Concretely:
- Row click drives the card; the dropdown goes away
- Visible columns cut 12 → 6 (`prty_id`, `nba_category`, `signal_strength`,
  `indicative_revenue_eur`, `why_now`, `rank`); the rest stay in the frame
  but hidden via `column_config` `None`
- `prty_id` pinned so it survives horizontal scroll
- `NumberColumn(format=…)` for revenue, `ProgressColumn` for strength —
  currently both render as bare numbers
- Card moves beside the table instead of below it

**Risk: medium.** This is the layout restructure you flagged as riskier.
It changes interaction, not just appearance, and I can't see the result.
Mitigation: the dropdown stays as a fallback if row-selection returns
nothing, so the page cannot become unusable.

---

## P3 — Wall of text

**Problem.** 11,504 characters. The rationale is *good* — it is what makes
the system auditable — but it is written at the altitude of a design doc,
inline, above every control.

**Proposed.** Keep every word; change where it lives.

| Now | Proposed |
|---|---|
| 537-char caption above the source picker | One line + `st.expander("How this works")` |
| Three-sentence caption per tab | One line; rest into the expander |
| Entitlement explanation (3 lines, every render) | `st.badge` showing scope + expander for the env-var detail |
| "Synthetic data only…" sidebar block | Keep — it is a disclosure, not decoration |

Target: ~11,500 → ~3,000 visible characters, with nothing deleted. The
disclosures CLAUDE.md requires (illustrative figures, synthetic data, NOT
RUN markers) all stay visible; it is the *explanatory* prose that folds.

**Risk: low-medium.** Purely presentational. The one judgement call is
which text is disclosure (must stay visible) vs explanation (can fold) —
I'd err toward keeping anything about data provenance or "illustrative"
figures visible.

---

## P4 — Ten sections, no entry point

**Problem.** Sidebar lists 10 peers in load order: Overview, Explore,
Onboard, ML, How it works, AWS, Verification proofs, Digests, Technique
reference, Status. A new user cannot tell that **Explore** is the product
and the other nine are supporting material.

**Proposed.** Group them, using `st.navigation`/`st.Page` sections (the
skill's recommended multipage pattern):

```
  WORK
    Explore a source        ← the product
    Digests
  SET UP
    Onboard a source
    ML opportunities
  UNDERSTAND
    Overview
    How it works
    Technique reference
  EVIDENCE
    Verification proofs
    AWS target architecture
    Status
```

Plus Material Symbols icons instead of the current emoji (`:material/search:`
rather than 🔍), per the skill's guidance.

**Risk: medium.** `st.navigation` changes how pages are dispatched;
`dashboard/app.py`'s `PAGES` dict and the tests that parametrise over it
(`tests/test_dashboard_pages_render.py`) would need updating together. Doable,
but it touches the app shell rather than one page.

**Cheaper alternative:** keep the current radio, just reorder it and add a
caption grouping. ~10 lines, near-zero risk, most of the benefit.

---

## What I'd do, and in what order

1. **P1 theme** — highest impact per unit of risk. Do this first and judge
   the result before anything else.
2. **P3 text** — big readability win, low risk.
3. **P2 worklist** — the real usability fix, but see it after P1 so you're
   judging layout rather than styling.
4. **P4 navigation** — or its cheap alternative.

Doing P1 alone and stopping is a legitimate outcome.

---

## What this proposal does NOT address

Worth stating plainly, because polish can disguise it: **none of this
makes the app multi-user or production-ready.** Identity is still
`local_dev` env vars, state is still local files, and onboarding still
writes into the repo's `config/`. A nicer UI on a single-user local demo
is still a single-user local demo.

If production for multiple users is the actual goal, that is a different
piece of work — server-side identity, a real database, per-tenant config,
run isolation — and it should not be confused with this.

---

## Open questions for you

1. **Dark or light?** The proposed theme is dark. Light variants available.
2. **P4: full `st.navigation` restructure, or the cheap reorder?**
3. **Anything in the prose you consider non-negotiable to keep visible?**
   My default is: all provenance and "illustrative" disclosures stay.
