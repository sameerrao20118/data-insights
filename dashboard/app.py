"""
DataInsights demo dashboard (Streamlit).

A local, mostly-read-only viewer over the same pipeline docs/user_guide.md
walks through on the command line -- built because a CLI transcript is a
poor way to show "where does the data come from, what does the output
look like" to someone watching over your shoulder. Everything here either
reads files this repo already writes (data_generator/output/,
external_events/output/, var/insights/, var/state.sqlite) or shells out to
the exact same modules the CLI calls -- no new detection, ranking, or
narrative logic lives here, and it never reads
data_generator/output/protected_evaluator_only/.

Run: streamlit run dashboard/app.py
"""

from __future__ import annotations

import subprocess
import sys
import sqlite3
import urllib.request
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data_generator" / "output"
EXT_DIR = ROOT / "external_events" / "output"
INSIGHTS_DIR = ROOT / "var" / "insights"
STATE_DB = ROOT / "var" / "state.sqlite"

st.set_page_config(page_title="DataInsights — demo dashboard", page_icon="🏦", layout="wide")

CATEGORY_INFO = {
    "FINANCING_NEED": ("Likely needs a loan, guarantee, or credit line", "#1F6E5C"),
    "TREASURY_OPPORTUNITY": ("Likely has surplus cash for deposit/investment", "#2C6FAA"),
    "HEDGING_NEED": ("Likely needs an FX/commodity hedge", "#A3791F"),
    "CAPEX_FINANCING": ("Likely needs equipment/transition financing", "#7A4FA0"),
    "RISK_REVIEW": ("Credit/risk awareness, not a sales opportunity", "#9C3D3D"),
    "ADVISORY_ONLY": ("Relationship conversation, no clear product yet", "#83897F"),
}

TECHNIQUE_ROWS = [
    ("Detection — transaction", "STATISTICAL", "Rolling median absolute deviation vs. trailing baseline"),
    ("Detection — external event", "RULE-BASED", "Sector/country match — a join, not a model"),
    ("Ranking (both pipelines)", "STATISTICAL", "Explicit magnitude + recency formula, every component visible"),
    ("Narrative (evidence → text)", "LLM (local Ollama)", "Validated against the evidence packet; template fallback on failure"),
    ("Quality check", "LLM (local Ollama)", "Sampled judge, deliberately a different model than the narrator"),
    ("Category tagging", "RULE-BASED", "Static/direction-conditional lookup table, not a model"),
    ("Not built yet", "MACHINE LEARNING", "Deferred — no real RM accept/reject outcomes exist yet to train against"),
]

EXTERNAL_SOURCE_MAP = [
    ("rate_policy_change", "ECB SDMX API", "Structured, real-time policy-rate series"),
    ("public_tender_award", "TED (Tenders Electronic Daily) API", "Structured award notices"),
    ("commodity_energy_shock", "Eurostat / ECB energy statistics", "Structured, but weekly-to-monthly lag"),
    ("sanctions_regulatory_change", "EU sanctions list / OpenSanctions", "Structured, authoritative"),
    ("eu_regulatory_change", "EUR-Lex", "Structured metadata; sector impact needs interpretation"),
    ("geopolitical_disruption", "GDELT Project", "Unstructured — needs LLM extraction first, noisier"),
    ("natural_disaster", "EM-DAT International Disaster Database", "Structured, but reporting lag of days-to-weeks"),
]


# ---------- data access (cached by file mtime, never by content assumption) ----------

@st.cache_data(show_spinner=False)
def _read_csv_cached(path_str: str, _mtime: float, nrows: int | None = None) -> pd.DataFrame:
    return pd.read_csv(path_str, nrows=nrows)


def read_csv(path: Path, nrows: int | None = None) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return _read_csv_cached(str(path), path.stat().st_mtime, nrows)


def count_rows(path: Path) -> int | None:
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return sum(1 for _ in f) - 1


def latest_file(directory: Path, pattern: str) -> Path | None:
    if not directory.exists():
        return None
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def ollama_reachable() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.0)
        return True
    except Exception:
        return False


def run_module(args: list[str], label: str) -> None:
    with st.spinner(f"Running: {label} ..."):
        proc = subprocess.run(
            [sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True,
        )
    st.session_state.setdefault("logs", [])
    st.session_state["logs"].insert(0, {
        "label": label, "ok": proc.returncode == 0,
        "out": proc.stdout[-4000:], "err": proc.stderr[-2000:],
    })
    st.cache_data.clear()


# ---------- sidebar ----------

with st.sidebar:
    st.markdown("### DataInsights")
    st.caption("Commercial/institutional NBA-EBM proof of concept")
    ok = ollama_reachable()
    st.markdown(f"**Local Ollama:** {'🟢 reachable' if ok else '🔴 not reachable'}")
    if not ok:
        st.caption("Narrative steps fall back to a deterministic template, per project policy — no cloud fallback ever.")
    st.divider()
    page = st.radio(
        "Section",
        ["Overview", "Data sources", "Run the pipeline", "Worklist", "Digests", "Technique reference", "Status"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(
        "Synthetic data only — no real client, transaction, or event data "
        "anywhere in this repo. This dashboard is a demo viewer, not the "
        "review/tracking system named as a future gap in docs/gap_analysis.md."
    )

st.title(page if page != "Overview" else "DataInsights — how this works")


# ---------- Overview ----------

if page == "Overview":
    st.markdown(
        "Relationship managers reach out on a calendar, not on a signal. This "
        "system watches two things a calendar can't: a client's **own** "
        "transaction behavior, and **external** market/political events near "
        "them — and turns both into one ranked, evidence-backed worklist."
    )
    cols = st.columns(5)
    steps = [
        ("1 · Source", "Configured data source", "Local CSV today, Snowflake later — same interface"),
        ("2 · Detect", "Statistical / rule-based", "MAD baseline (transactions) or sector/country match (events)"),
        ("3 · Rank", "Statistical", "Magnitude + recency, every component visible — no ML yet"),
        ("4 · Narrate", "Local LLM", "Validated against evidence; template fallback on failure"),
        ("5 · Digest", "RM-facing output", "Markdown digest + one CSV worklist, both pipelines unified"),
    ]
    for col, (num, title, sub) in zip(cols, steps):
        with col:
            st.markdown(f"**{num}**")
            st.markdown(f"`{title}`")
            st.caption(sub)
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Endogenous — the client's own data")
        st.write("A client's own transactions, out of pattern for them specifically.")
        st.caption("Detector: `detection_engine/large_incoming_payment.py`")
    with c2:
        st.subheader("Exogenous — the world around them")
        st.write("Market/political/industry events, matched by sector + country.")
        st.caption("Detector: `detection_engine/external_macro_event.py`")
    st.divider()
    st.info(
        "Full write-up: `docs/architecture.md` · presentation diagram: "
        "`docs/artifacts/pipeline-blueprint.html` · live-demo script: "
        "`docs/artifacts/demo-run-sheet.html`",
        icon="📎",
    )


# ---------- Data sources ----------

elif page == "Data sources":
    st.subheader("Internal (endogenous) — a client's own data")
    st.caption("Read today from local CSV via `OfflineLocalSource` (DuckDB). Same `DataSource` interface, `SnowflakeSource`, is wired but NOT RUN — no credentials configured.")
    internal_files = [
        ("clients.csv", "300 synthetic commercial/institutional clients — LEI, NACE sector, group hierarchy, PEP/sanctions fields"),
        ("accounts.csv", "Client accounts, structurally valid IBAN/BIC, including realistic closures"),
        ("transactions.csv", "Booking/value date, ISO 20022 purpose codes, message types, bank fees"),
        ("facilities.csv", "Loans, credit lines, trade finance, guarantees"),
        ("balances.csv", "End-of-day balance snapshots"),
    ]
    for fname, desc in internal_files:
        path = DATA_DIR / fname
        n = count_rows(path)
        with st.expander(f"**{fname}** — {n:,} rows" if n is not None else f"**{fname}** — not found", expanded=False):
            st.caption(desc)
            df = read_csv(path, nrows=20)
            if df is not None:
                st.dataframe(df, width="stretch", hide_index=True)
            else:
                st.warning("Not generated yet — run `python data_generator/generate_data.py`.")

    st.divider()
    st.subheader("External (exogenous) — market/political/industry events")
    st.caption(
        "Simulated today (`SimulatedExternalEventSource`); the interface is designed so a real "
        "adapter for any row below can be dropped in later without touching detector code."
    )
    ext_path = EXT_DIR / "external_events.csv"
    n_ext = count_rows(ext_path)
    st.metric("Simulated events available", n_ext if n_ext is not None else "—")
    map_df = pd.DataFrame(EXTERNAL_SOURCE_MAP, columns=["Event type", "Real free API it maps to", "What that source actually brings"])
    st.dataframe(map_df, width="stretch", hide_index=True)
    df_ext = read_csv(ext_path, nrows=15)
    if df_ext is not None:
        st.caption("Sample of simulated events:")
        st.dataframe(df_ext, width="stretch", hide_index=True)
    else:
        st.warning("Not generated yet — run `python -m external_events.simulate_external_events`.")

    st.divider()
    st.subheader("Local machine now vs. Snowflake later")
    compare_df = pd.DataFrame([
        ("Source", "DuckDB over CSV", "SnowflakeSource, same DataSource interface"),
        ("Detector / ranking / narrative code", "Unchanged", "Unchanged — that's the point"),
        ("External events", "Simulated CSV", "External Access Integration → real ECB/TED/GDELT APIs, server-side"),
        ("State", "SQLite", "Same, or a remote store if concurrency needs it"),
        ("LLM", "Local Ollama", "Local Ollama — never Cortex/Bedrock, by policy"),
    ], columns=["", "Today (local)", "Later (Snowflake)"])
    st.dataframe(compare_df, width="stretch", hide_index=True)
    st.caption("Full comparison: `docs/artifacts/deployment-options.html` · setup steps: `docs/snowflake_setup.md`")


# ---------- Run the pipeline ----------

elif page == "Run the pipeline":
    st.caption(
        "Each button below runs the exact module the CLI runs — nothing here is separate logic. "
        "Runs are idempotent: no duplicate detections, cached narratives are reused."
    )
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("① Run tests", width="stretch"):
            run_module(["pytest", "tests/", "-q"], "test suite")
    with c2:
        if st.button("② Endogenous pipeline", width="stretch"):
            run_module(["datainsights.cli", "--max-narratives", "15"], "endogenous pipeline")
    with c3:
        if st.button("③ Exogenous pipeline", width="stretch"):
            run_module(["external_events.demo_scenario"], "exogenous pipeline")
    with c4:
        if st.button("④ Build worklist", width="stretch"):
            run_module(["datainsights.build_worklist"], "worklist build")

    st.divider()
    logs = st.session_state.get("logs", [])
    if not logs:
        st.caption("No runs yet this session. Click a button above, or use the existing files already in the repo — nothing here requires a fresh run to explore the other tabs.")
    for entry in logs:
        icon = "✅" if entry["ok"] else "❌"
        with st.expander(f"{icon} {entry['label']}", expanded=(entry is logs[0])):
            if entry["out"]:
                st.code(entry["out"], language="text")
            if entry["err"]:
                st.code(entry["err"], language="text")


# ---------- Worklist ----------

elif page == "Worklist":
    wl_path = latest_file(INSIGHTS_DIR, "worklist_*.csv")
    if wl_path is None:
        st.warning("No worklist yet — run it from the **Run the pipeline** tab, or `python -m datainsights.build_worklist`.")
    else:
        df = read_csv(wl_path)
        st.caption(f"Reading `{wl_path.relative_to(ROOT)}` — {len(df):,} rows, one per (client, active recommendation).")

        counts = df["category"].value_counts()
        metric_cols = st.columns(len(CATEGORY_INFO))
        for col, (cat, (desc, color)) in zip(metric_cols, CATEGORY_INFO.items()):
            with col:
                st.metric(cat.replace("_", " ").title(), int(counts.get(cat, 0)))
                st.caption(desc)
        st.bar_chart(counts)

        st.divider()
        f1, f2, f3 = st.columns(3)
        with f1:
            cat_filter = st.multiselect("Category", sorted(df["category"].unique()))
        with f2:
            sector_filter = st.multiselect("Sector", sorted(df["sector"].dropna().unique()))
        with f3:
            country_filter = st.multiselect("Country", sorted(df["country"].dropna().unique()))

        filtered = df.copy()
        if cat_filter:
            filtered = filtered[filtered["category"].isin(cat_filter)]
        if sector_filter:
            filtered = filtered[filtered["sector"].isin(sector_filter)]
        if country_filter:
            filtered = filtered[filtered["country"].isin(country_filter)]

        st.dataframe(
            filtered[["rank", "score", "category", "client_id", "legal_name", "sector",
                      "country", "relationship_manager_id", "event_type", "recommended_action"]],
            width="stretch", hide_index=True, height=380,
        )

        st.divider()
        st.subheader("Inspect one recommendation")
        options = filtered["detection_id"].tolist() if len(filtered) else df["detection_id"].tolist()
        if options:
            chosen = st.selectbox("Detection", options, format_func=lambda d: d)
            row = df[df["detection_id"] == chosen].iloc[0]
            st.markdown(f"**{row['legal_name']}** ({row['client_id']}) — rank {row['rank']}, score {row['score']:.2f}")
            st.markdown(f"Category: **{row['category']}** · Event: `{row['event_type']}` · RM: `{row['relationship_manager_id']}`")
            st.markdown(f"**Recommended action:** {row['recommended_action']}")
            st.markdown(f"**Evidence:** {row['evidence_summary']}")
            st.caption(f"Narrative source: {row['narrative_source']} · Event date: {row['event_date']}")


# ---------- Digests ----------

elif page == "Digests":
    digest_files = sorted(INSIGHTS_DIR.glob("digest_*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if INSIGHTS_DIR.exists() else []
    if not digest_files:
        st.warning("No digest yet — run a pipeline from the **Run the pipeline** tab.")
    else:
        names = [p.name for p in digest_files]
        chosen = st.selectbox("Digest file", names)
        content = (INSIGHTS_DIR / chosen).read_text()
        st.markdown(content)


# ---------- Technique reference ----------

elif page == "Technique reference":
    st.caption("Where each kind of logic sits, and why the rest isn't ML yet.")
    tech_df = pd.DataFrame(TECHNIQUE_ROWS, columns=["Stage", "Technique", "Why"])
    st.dataframe(tech_df, width="stretch", hide_index=True)
    st.divider()
    st.subheader("The six recommendation categories")
    for cat, (desc, color) in CATEGORY_INFO.items():
        st.markdown(f"**{cat.replace('_', ' ').title()}** — {desc}")


# ---------- Status ----------

elif page == "Status":
    if not STATE_DB.exists():
        st.warning("No state database yet — run the endogenous pipeline at least once.")
    else:
        con = sqlite3.connect(STATE_DB)
        last_run = con.execute(
            "SELECT run_id, started_at, finished_at, mode, profile, status FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        n_active = con.execute("SELECT COUNT(*) FROM detections WHERE status='detected'").fetchone()[0]
        n_narratives = con.execute("SELECT COUNT(*) FROM narratives").fetchone()[0]
        n_runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        con.close()

        if last_run:
            run_id, started, finished, mode, profile, run_status = last_run
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total runs recorded", n_runs)
            c2.metric("Active detections", n_active)
            c3.metric("Cached narratives", n_narratives)
            c4.metric("Last run status", run_status)
            st.caption(f"Last run: `{run_id}` ({mode}/{profile}) — started {started}, finished {finished or 'still running / crashed'}")
        else:
            st.info("No runs recorded yet.")
        st.caption(
            "This is a manual/replay POC, not 24/7 monitoring — there is no background "
            "process. State persists between runs; nothing here auto-refreshes."
        )
