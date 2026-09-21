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

R22 (docs/refactor_plan.md §6k): this module holds everything the pages
share -- paths, cached readers, the diagrams, DATA_SOURCES, CATEGORY_INFO,
run_module -- and is imported once by dashboard/app.py before any page
renders. Pages live in dashboard/tabs/<page>.py, one render() each.
"""

from __future__ import annotations

import subprocess
import sys
import sqlite3  # noqa: F401 -- used by page modules via `from dashboard.common import *`
import urllib.request
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
# `streamlit run dashboard/app.py` puts this file's own directory on
# sys.path, not the repo root -- so `from datainsights...`/`from agents...`
# (used lazily throughout this file, e.g. the RM copilot and feedback
# sections) only worked before by accident, depending on cwd/PYTHONPATH at
# launch time. Make it work regardless of how this script is invoked.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIR = ROOT / "data_generator" / "output"      # the legacy-schema dataset (config/bindings/legacy.yaml)
EXT_DIR = ROOT / "external_events"                  # event feeds live under output_<schema>/
INSIGHTS_DIR = ROOT / "var" / "insights"
# (FDM_DATA_DIR removed: the client pickers that used it now read through
# CanonicalSource, so no module-level constant points at one schema's data.)
TRACE_DB = ROOT / "var" / "agent_traces.db"   # R23: the agentic run record (datainsights/agent_trace.py)

st.set_page_config(page_title="DataInsights — demo dashboard", page_icon="🏦", layout="wide")

# R2: description/colour come from config/categories.yaml via the registry.
# The (description, colour) tuple shape is kept so the use sites are
# unchanged -- but the content is config, not a Python dict.
from datainsights import category_registry as _category_registry  # noqa: E402

CATEGORY_INFO = {c: (_category_registry.description(c), _category_registry.colour(c))
                 for c in _category_registry.category_names()}

# Display-only: which canonical concepts each domain agent reads. There is
# no registry of "concepts per domain" -- agents/tools.py's factories read
# them inline -- so this is maintained by hand for the lineage view. If a
# tool starts reading a new concept, update this too: a stale entry here
# misrepresents provenance, which is worse than showing nothing.
DOMAIN_READS = {
    "deposits": ["Account", "BalanceObservation", "Transaction"],
    "lending": ["Account", "BalanceObservation", "CollateralValuation"],
    "risk": ["RiskGradeVersion"],
    "exogenous": ["Account", "Transaction"],  # deposits also reads Transaction for large_incoming_payment
}

# ---------- data sources: DISCOVERED from config/profiles/, not listed ----------
#
# This used to be a hand-maintained dict, which meant onboarding a schema
# (onboarding/accept.py writes a binding, a contract and a profile) left it
# invisible in the dashboard until someone also hand-added an entry here.
# Two schemas were mislabelled `proof_only` for exactly that reason long
# after the pipeline could run them -- legacy produces 167 recommendations
# and SBA 131, both verified by running them.
#
# Now: every profile in config/profiles/ that can actually drive the tabs
# shows up on its own. `kind` is COMPUTED from capability rather than
# declared, so it cannot go stale:
#
#   full_agentic  <- has a binding, a local data_dir, and that binding
#                    supplies the picker concept (Party). The three tabs
#                    can genuinely run.
#   proof_only    <- a profile that cannot drive them (no binding, no local
#                    data_dir, or Party unavailable), but has a proof test.
#   hidden        <- cannot drive the tabs and has no proof to show.
#
# CURATED_SOURCES below is presentation only -- a nicer label, a
# description, a proof test to offer. Absent curation, a profile still
# appears with a generated label. Nothing here gates functionality.

CURATED_SOURCES = {
    "fdm_local": {
        "key": "fdm",
        "label": "FDM synthetic book",
        # FDM splits tables across kernel/ and lending/; the ML scan wants the
        # non-recursive root, the data browser wants the whole tree.
        "data_dir": ROOT / "data_generator" / "output_fdm" / "kernel",
        "description": (
            "Bi-temporal Federated Data Model shape -- 60 synthetic commercial clients, "
            "deposits/lending/risk domains, plus a tender-award exogenous event feed. "
            "Runs through agents/orchestrator.py: whole-book worklist, live per-client "
            "narration, RM copilot, feedback capture."
        ),
    },
    "legacy_local": {
        "key": "legacy",
        "label": "Legacy schema",
        "description": (
            "A structurally different schema (no bi-temporal versions, different table "
            "names) bound through config/bindings/legacy.yaml. R23 retired the original "
            "pipeline: the SAME agents/orchestrator.py path now runs it -- "
            "`python -m agents.demo_fdm_scenario --profile legacy_local` produces its worklist."
        ),
        "proof_test": "tests/test_legacy_binding_end_to_end.py",
        "proof_label": "Schema portability proof",
        "proof_success": (
            "Passed -- deposits/lending detectors fire real signals against the second schema "
            "through the same canonical read path; unavailable concepts are reported, not crashed."
        ),
    },
    "sba_local": {
        "key": "sba",
        "label": "SBA — real commercial data",
        "description": (
            "REAL U.S. Small Business Administration PPP loan entities/sectors/loans "
            "(24 real NAICS sectors), synthetic deposit activity layered on top."
        ),
        "proof_test": "tests/test_sba_binding_end_to_end.py",
        "proof_label": "Real-data schema proof",
        "proof_success": (
            "6/6 passed — real sector diversity confirmed (≥10 distinct NAICS codes); "
            "deposits/lending detectors fire real signals; risk detectors correctly "
            "report unavailability (PPP loans have no continuous rating history and "
            "aren't collateralised) instead of crashing."
        ),
    },
}

# The canonical concept the client pickers read. A binding that cannot
# supply it cannot drive the per-client tabs.
PICKER_CONCEPT = "Party"


def _profile_capability(profile_name: str) -> tuple[str, dict]:
    """(kind, derived fields) for one profile, by asking what it can do.

    Never raises: a profile that cannot be loaded or whose binding is
    broken is reported as hidden, because a dashboard that crashes on one
    bad profile is worse than one that omits it."""
    try:
        from datainsights.runtime import active_profile

        profile = active_profile(profile_name)
    except Exception:  # noqa: BLE001 -- an unloadable profile is hidden, not fatal
        return "hidden", {}

    binding_name = getattr(profile.source, "binding", None)
    data_dir = getattr(profile.source, "data_dir", None)
    derived = {"profile": profile_name, "binding": binding_name}
    if data_dir:
        derived["data_root"] = ROOT / data_dir
        derived["data_dir"] = ROOT / data_dir

    # A remote-backed profile (Snowflake, S3) has no local directory to
    # browse and no verified run behind it -- it self-excludes here rather
    # than needing a hardcoded skip list.
    if not binding_name or not data_dir:
        return "hidden", derived

    try:
        from datainsights.semantic.binding import load_binding

        binding = load_binding(binding_name)
        concept = binding.concepts.get(PICKER_CONCEPT)
        if concept is None or concept.unavailable:
            return "proof_only", derived
    except Exception:  # noqa: BLE001 -- a broken binding is proof_only at best
        return "proof_only", derived

    derived["picker_concept"] = PICKER_CONCEPT
    return "full_agentic", derived


def _discover_data_sources() -> dict:
    """Every profile that can show something, keyed for the UI."""
    profiles_dir = ROOT / "config" / "profiles"
    discovered: dict = {}
    for path in sorted(profiles_dir.glob("*.yaml")):
        profile_name = path.stem
        kind, derived = _profile_capability(profile_name)
        curated = dict(CURATED_SOURCES.get(profile_name, {}))
        key = curated.pop("key", profile_name)

        if kind == "hidden" and not curated.get("proof_test"):
            continue
        if kind == "hidden":
            kind = "proof_only"

        entry = {
            "label": curated.pop("label", profile_name.replace("_", " ").title()),
            "kind": kind,
            "description": curated.pop(
                "description",
                f"Discovered from config/profiles/{profile_name}.yaml "
                f"(binding: {derived.get('binding')}). No curated description.",
            ),
            **derived,
        }
        entry.update(curated)          # proof_test/label/success, data_dir override
        discovered[key] = entry
    return discovered


DATA_SOURCES = _discover_data_sources()



def worklist_path_for(profile: str):
    """Where a profile's whole-book worklist CSV lands.

    agents/demo_fdm_scenario.py writes `fdm_rm_worklist.csv` into the
    profile's own out_dir, and every local profile shares
    var/insights -- so running two profiles overwrote each other's
    worklist. The dashboard now reads a per-profile copy, and the run
    button snapshots the generated file to that name. The generator's own
    output path is deliberately left alone: changing it would break
    docs/current_state.md's documented commands and every existing
    reference to var/insights/fdm_rm_worklist.csv."""
    return INSIGHTS_DIR / f"worklist_{profile}.csv"


TECHNIQUE_ROWS = [
    ("Detection — transaction", "STATISTICAL", "Rolling median absolute deviation vs. trailing baseline"),
    ("Detection — external event", "RULE-BASED", "Sector/country match — a join, not a model"),
    ("Ranking", "STATISTICAL", "Explicit magnitude + recency formula, every component visible"),
    ("Narrative (evidence → text)", "LLM (local Ollama)", "Validated against the evidence packet; template fallback on failure"),
    ("Quality check", "LLM (local Ollama)", "Sampled judge, deliberately a different model than the narrator"),
    ("Category tagging", "RULE-BASED", "Static/direction-conditional lookup table, not a model"),
    ("Client baseline challenger (FDM)", "MACHINE LEARNING",
     "Opt-in IsolationForest baseline vs. deterministic benchmark (datainsights/ml/) — not the production default"),
    ("Propensity / ranking", "NOT BUILT", "Blocked on RM response data access (D6); arbitration belongs to Pega"),
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


def save_ml_policy(schema: str, edits: dict[str, tuple[bool, str]]) -> None:
    """Writes config/ml_policy.yaml -- the ONLY place a dashboard click
    writes pipeline configuration outside onboarding/accept.py's own
    Accept step. Merges into the existing file (preserves other
    schemas/measures and any hand-set hyperparameters/disabled_reason
    untouched by this save) rather than overwriting it wholesale."""
    import yaml as _yaml

    path = ROOT / "config" / "ml_policy.yaml"
    raw, header = {}, ""
    if path.exists():
        text = path.read_text()
        # The leading comment block carries the R7 power-criteria reasoning --
        # load-bearing documentation, not decoration. A yaml round-trip drops
        # every comment, so capture the header here and re-prepend it on
        # write. Found the hard way: a Save wiped it and tests/test_ml_gate.py
        # caught the loss on the next run.
        lines = text.splitlines(keepends=True)
        cut = len(lines)
        for i, line in enumerate(lines):
            if line.strip() and not line.lstrip().startswith("#"):
                cut = i
                break
        header = "".join(lines[:cut])
        raw = _yaml.safe_load(text) or {}
    raw.setdefault("schemas", {}).setdefault(schema, {}).setdefault("measures", {})
    schema_measures = raw["schemas"][schema]["measures"]
    for measure, (enabled, algorithm) in edits.items():
        existing = schema_measures.get(measure, {})
        schema_measures[measure] = {
            "enabled": enabled, "algorithm": algorithm, "chosen_by": "policy",
            "hyperparameters": existing.get("hyperparameters", {}),
        }
        if not enabled:
            schema_measures[measure]["disabled_reason"] = existing.get(
                "disabled_reason", "disabled from the dashboard's ML opportunities tab")
    with open(path, "w") as f:
        f.write(header)
        _yaml.safe_dump(raw, f, sort_keys=False)


# ---------- agent flow diagram (static HTML/CSS, no external deps) ---------
#
# Traces agents/orchestrator.py's evaluate_client() literally: the one
# function AgentCore, a batch job, and every demo script all call. Built
# by hand rather than via st.graphviz_chart -- this venv/host has neither
# the graphviz Python package nor the system `dot` binary, and a live
# demo shouldn't depend on either being present.

AGENT_FLOW_HTML = """
<div class="af-wrap">
<style>
  .af-wrap { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
             background:#fbfaf7; color:#1c2a2e; padding:20px 10px 28px; border-radius:10px; }
  .af-wrap * { box-sizing: border-box; }
  .af-legend { display:flex; flex-wrap:wrap; gap:18px; justify-content:center; margin-bottom:22px;
               font-size:12px; color:#52646a; }
  .af-legend span.sw { display:inline-block; width:22px; height:0; border-top:2.5px solid #1f6f64;
                        vertical-align:middle; margin-right:6px; }
  .af-legend span.sw.dash { border-top-style:dashed; border-color:#a3791f; }
  .af-legend span.badge { background:#e9e2f5; color:#5b3fa0; border:1px solid #cdbcec; border-radius:4px;
                           padding:1px 6px; font-weight:600; font-size:10.5px; margin-right:4px; }
  .af-stage { display:flex; flex-direction:column; align-items:center; }
  .af-box { background:#ffffff; border:1.5px solid #c7c0ac; border-radius:9px; padding:12px 16px;
            box-shadow:0 1px 2px rgba(28,42,46,0.07); text-align:center; }
  .af-box h4 { margin:0 0 4px; font-size:13.5px; color:#1c2a2e; }
  .af-box p { margin:0; font-size:11.3px; color:#5c6b68; line-height:1.45; }
  .af-box code { background:#eef0eb; border-radius:3px; padding:0 3px; font-size:10.8px; }
  .af-data { width:min(880px,96%); }
  .af-arrow-down { width:2px; height:22px; background:#9fb0ac; margin:0 auto; position:relative; }
  .af-arrow-down::after { content:""; position:absolute; bottom:-1px; left:50%; transform:translateX(-50%);
                           border:5px solid transparent; border-top-color:#9fb0ac; }
  .af-bus { width:min(1000px,97%); position:relative; height:22px; }
  .af-bus .line { position:absolute; top:0; left:6%; right:6%; height:2px; background:#9fb0ac; }
  .af-bus .drop { position:absolute; top:0; width:2px; height:22px; background:#9fb0ac; }
  .af-bus .drop::after { content:""; position:absolute; bottom:-1px; left:50%; transform:translateX(-50%);
                          border:5px solid transparent; border-top-color:#9fb0ac; }
  .af-row4 { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; width:min(1000px,97%); }
  .af-agent { display:flex; flex-direction:column; align-items:center; gap:0; }
  .af-agent .af-box { width:100%; min-height:118px; border-color:#bcd8cd; }
  .af-agent .af-box.exo { border-color:#e4c98a; }
  .af-tool-hint { font-size:10px; color:#8a978f; margin-top:6px; }
  .af-mlbadge { display:inline-block; margin-top:5px; background:#e9e2f5; color:#5b3fa0;
                border:1px solid #cdbcec; border-radius:4px; padding:1px 6px; font-weight:600; font-size:9.5px; }
  .af-ollama-lane { margin-top:8px; display:flex; flex-direction:column; align-items:center; }
  .af-ollama-lane .dash { width:2px; height:16px; border-left:2px dashed #c9a74a; }
  .af-ollama-chip { font-size:9.5px; color:#8a6d1e; background:#faf3e1; border:1px dashed #e0c47c;
                    border-radius:10px; padding:2px 8px; margin-top:2px; }
  .af-converge { width:min(1000px,97%); position:relative; height:26px; }
  .af-converge .rise { position:absolute; top:0; width:2px; height:14px; background:#9fb0ac; }
  .af-converge .line { position:absolute; top:14px; left:6%; right:6%; height:2px; background:#9fb0ac; }
  .af-converge .down { position:absolute; top:14px; left:50%; width:2px; height:12px; background:#9fb0ac;
                        transform:translateX(-50%); }
  .af-converge .down::after { content:""; position:absolute; bottom:-1px; left:50%; transform:translateX(-50%);
                               border:5px solid transparent; border-top-color:#9fb0ac; }
  .af-single { width:min(560px,94%); }
  .af-single .af-box { border-color:#1f6f64; }
  .af-out-row { display:grid; grid-template-columns:repeat(5,1fr); gap:12px; width:min(1040px,98%); }
  .af-out-row .af-box { min-height:64px; }
  .af-out-row .af-box p { font-size:10.3px; }
</style>

  <div class="af-legend">
    <div><span class="sw"></span>deterministic data flow</div>
    <div><span class="sw dash"></span>LLM narration (local Ollama, <code>narrate=True</code> only)</div>
    <div><span class="badge">ML</span>opt-in statistical baseline (SLOT E2)</div>
  </div>

  <div class="af-stage">
    <div class="af-box af-data">
      <h4>1 · Canonical data layer</h4>
      <p><code>CanonicalSource</code> reads through <code>config/bindings/fdm.yaml</code> (or <code>legacy.yaml</code>)
      onto physical data in <code>data_generator/output_fdm/</code> — plus the exogenous event registry,
      <code>config/event_types.yaml</code> (tender award, FX-rate move).</p>
    </div>

    <div class="af-arrow-down"></div>

    <div class="af-bus"><div class="line"></div>
      <div class="drop" style="left:12.5%"></div><div class="drop" style="left:37.5%"></div>
      <div class="drop" style="left:62.5%"></div><div class="drop" style="left:87.5%"></div>
    </div>

    <div class="af-row4">
      <div class="af-agent">
        <div class="af-box"><h4>Deposits agent</h4>
          <p><code>agents/domain_agent.py</code> · tools: <code>check_cash_buildup</code>,
          <code>check_dormancy</code>, <code>check_revenue_pattern_change</code></p>
          <p class="af-tool-hint">→ <code>detection_engine/cash_buildup.py</code>,
          <code>dormancy.py</code>, <code>revenue_pattern_change.py</code></p>
          <span class="af-mlbadge">ML: IsolationForest opt-in (cash_buildup, revenue_pattern_change)</span>
        </div>
        <div class="af-ollama-lane"><div class="dash"></div><div class="af-ollama-chip">narrates via local Ollama</div></div>
      </div>
      <div class="af-agent">
        <div class="af-box"><h4>Lending agent</h4>
          <p>tools: <code>check_facility_utilization_spike</code>, <code>check_facility_maturity</code>,
          <code>check_fixed_rate_expiry</code>, <code>check_collateral_coverage</code></p>
          <p class="af-tool-hint">→ <code>detection_engine/facility_*.py</code>, <code>fixed_rate_expiry.py</code>,
          <code>collateral_coverage_drop.py</code></p>
        </div>
        <div class="af-ollama-lane"><div class="dash"></div><div class="af-ollama-chip">narrates via local Ollama</div></div>
      </div>
      <div class="af-agent">
        <div class="af-box"><h4>Risk agent</h4>
          <p>tools: <code>check_rating_downgrade</code>, <code>check_pd_migration</code></p>
          <p class="af-tool-hint">→ <code>detection_engine/rating_downgrade.py</code>, <code>pd_migration.py</code></p>
        </div>
        <div class="af-ollama-lane"><div class="dash"></div><div class="af-ollama-chip">narrates via local Ollama</div></div>
      </div>
      <div class="af-agent">
        <div class="af-box exo"><h4>Exogenous agent</h4>
          <p>tool: <code>check_exogenous_exposure</code> — qualifies a matched event
          (<code>external_events/exposure_qualifier.py</code>) against this client's OWN activity,
          never a sector/country broadcast</p>
        </div>
        <div class="af-ollama-lane"><div class="dash"></div><div class="af-ollama-chip">narrates via local Ollama</div></div>
      </div>
    </div>

    <div class="af-converge">
      <div class="rise" style="left:12.5%"></div><div class="rise" style="left:37.5%"></div>
      <div class="rise" style="left:62.5%"></div><div class="rise" style="left:87.5%"></div>
      <div class="line"></div><div class="down"></div>
    </div>

    <div class="af-stage af-single">
      <div class="af-box"><h4>2 · Signal Bus</h4>
        <p><code>agents/orchestrator.py::signals_from_tool_evidence()</code> — every domain's
        <code>detected</code> tool evidence becomes one typed <code>Signal</code>, grouped by client.
        Purely deterministic: the LLM narrations above never reach this step.</p>
      </div>
      <div class="af-arrow-down"></div>
      <div class="af-box"><h4>3 · Hypothesis Assembler</h4>
        <p><code>datainsights/correlation/hypothesis.py::assemble()</code> — picks the strongest signal,
        applies the multi-domain confirmation curve, folds in exogenous confirmation
        (<code>config/event_types.yaml</code>'s <code>correlation</code> block) and the
        <code>HIGH_RSK_CUST_IND</code> suppression rule, sizes the offer. One category, one hypothesis,
        one sized <code>Recommendation</code> — decided here, not by any LLM.</p>
      </div>
      <div class="af-arrow-down"></div>
    </div>

    <div class="af-out-row">
      <div class="af-box"><h4>RM worklist CSV</h4><p><code>var/insights/fdm_rm_worklist.csv</code></p></div>
      <div class="af-box"><h4>RM digest</h4><p><code>fdm_rm_digest.md</code></p></div>
      <div class="af-box"><h4>Dashboard</h4><p>this app's Explore a source tab</p></div>
      <div class="af-box"><h4>MIMO JSON</h4><p>schema-only, never sent</p></div>
      <div class="af-box"><h4>Pega event mock</h4><p>schema-only, never sent</p></div>
    </div>
  </div>
</div>
"""


# ---------- AgentCore vision diagram (static HTML/CSS, no external deps) ---
#
# NOT a second implementation -- a labelled overlay of the SAME diagram
# above, showing which box swaps for what per docs/decision_record.md's
# "AgentCore staging -- D4 in practice" (Tab 5) and the 5-phase governance
# gate (also Tab 5). Every AWS-side box here is CONTRACT-ONLY / NOT RUN
# per CLAUDE.md until that gate is actually passed -- this view exists so
# a stakeholder can see the target shape, not to claim it's built.

AGENTCORE_VISION_HTML = """
<div class="af-wrap">
<style>
  .af-wrap { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
             background:#fbfaf7; color:#1c2a2e; padding:20px 10px 28px; border-radius:10px; }
  .af-wrap * { box-sizing: border-box; }
  .af-legend { display:flex; flex-wrap:wrap; gap:16px; justify-content:center; margin-bottom:18px;
               font-size:11.5px; color:#52646a; }
  .af-legend span.sw { display:inline-block; width:22px; height:0; border-top:2.5px solid #1f6f64;
                        vertical-align:middle; margin-right:6px; }
  .af-legend span.sw.dash { border-top-style:dashed; border-color:#8a97a3; }
  .af-legend span.badge { border-radius:4px; padding:1px 6px; font-weight:600; font-size:10px; margin-right:4px; }
  .af-legend span.badge.gate { background:#faf3e1; color:#8a6d1e; border:1px solid #e0c47c; }
  .af-legend span.badge.cand { background:#e9e2f5; color:#5b3fa0; border:1px solid #cdbcec; }
  .af-gate-banner { width:min(1040px,98%); margin:0 auto 20px; background:#fbf1e0; border:1.5px solid #e0c47c;
                     border-radius:9px; padding:10px 16px; text-align:center; }
  .af-gate-banner b { color:#7a5c14; }
  .af-gate-banner p { margin:4px 0 0; font-size:11px; color:#7a5c14; line-height:1.5; }
  .af-stage { display:flex; flex-direction:column; align-items:center; }
  .af-box { background:#ffffff; border:1.5px solid #c7c0ac; border-radius:9px; padding:12px 16px;
            box-shadow:0 1px 2px rgba(28,42,46,0.07); text-align:center; }
  .af-box.future { border-style:dashed; border-color:#8a97a3; }
  .af-box.candidate { border-style:dashed; border-color:#cdbcec; }
  .af-box h4 { margin:0 0 4px; font-size:13.5px; color:#1c2a2e; }
  .af-box p { margin:0; font-size:11.3px; color:#5c6b68; line-height:1.45; }
  .af-box code { background:#eef0eb; border-radius:3px; padding:0 3px; font-size:10.8px; }
  .af-data { width:min(880px,96%); }
  .af-arrow-down { width:2px; height:22px; background:#9fb0ac; margin:0 auto; position:relative; }
  .af-arrow-down::after { content:""; position:absolute; bottom:-1px; left:50%; transform:translateX(-50%);
                           border:5px solid transparent; border-top-color:#9fb0ac; }
  .af-arrow-down.dash { background:none; border-left:2px dashed #8a97a3; width:0; }
  .af-row2 { display:grid; grid-template-columns:repeat(2,1fr); gap:16px; width:min(760px,90%); }
  .af-bus { width:min(1000px,97%); position:relative; height:22px; }
  .af-bus .line { position:absolute; top:0; left:6%; right:6%; height:2px; background:#9fb0ac; }
  .af-bus .drop { position:absolute; top:0; width:2px; height:22px; background:#9fb0ac; }
  .af-bus .drop::after { content:""; position:absolute; bottom:-1px; left:50%; transform:translateX(-50%);
                          border:5px solid transparent; border-top-color:#9fb0ac; }
  .af-row4 { display:grid; grid-template-columns:repeat(4,1fr); gap:16px; width:min(1000px,97%); }
  .af-agent { display:flex; flex-direction:column; align-items:center; gap:0; }
  .af-agent .af-box { width:100%; min-height:128px; border-color:#bcd8cd; }
  .af-agent .af-box.exo { border-color:#e4c98a; }
  .af-tool-hint { font-size:10px; color:#8a978f; margin-top:6px; }
  .af-runtime-chip { display:inline-block; margin-top:6px; font-size:9.5px; color:#3a4a6b;
                      background:#eaf0fa; border:1px dashed #a9bfe0; border-radius:10px; padding:2px 8px; }
  .af-converge { width:min(1000px,97%); position:relative; height:26px; }
  .af-converge .rise { position:absolute; top:0; width:2px; height:14px; background:#9fb0ac; }
  .af-converge .line { position:absolute; top:14px; left:6%; right:6%; height:2px; background:#9fb0ac; }
  .af-converge .down { position:absolute; top:14px; left:50%; width:2px; height:12px; background:#9fb0ac;
                        transform:translateX(-50%); }
  .af-converge .down::after { content:""; position:absolute; bottom:-1px; left:50%; transform:translateX(-50%);
                               border:5px solid transparent; border-top-color:#9fb0ac; }
  .af-single { width:min(620px,94%); }
  .af-single .af-box { border-color:#1f6f64; }
  .af-single .af-box .unchanged { display:inline-block; margin-top:6px; font-size:9.5px; color:#1f6f64;
                                   background:#e7f3ee; border:1px solid #bcd8cd; border-radius:10px;
                                   padding:2px 8px; font-weight:600; }
  .af-out-row { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; width:min(880px,96%); }
  .af-out-row .af-box { min-height:70px; }
  .af-out-row .af-box p { font-size:10.3px; }
  .af-notrun { display:inline-block; margin-top:6px; font-size:9px; color:#8a4b1e; background:#fbeadb;
               border:1px solid #e6bd94; border-radius:10px; padding:1px 7px; font-weight:600; }
</style>

  <div class="af-legend">
    <div><span class="sw"></span>same code, unchanged</div>
    <div><span class="sw dash"></span>AWS-side swap of an existing local piece</div>
    <div><span class="badge gate">GATE</span>5-phase AgentCore governance checklist</div>
    <div><span class="badge cand">CANDIDATE</span>proposed, not yet selected or approved</div>
  </div>

  <div class="af-gate-banner">
    <b>Nothing below this banner runs today.</b>
    <p>Every AWS/AgentCore box on this page is CONTRACT-ONLY / NOT RUN per <code>CLAUDE.md</code> and
    <code>docs/decision_record.md</code> Tab&nbsp;5, until the 5-phase governance gate (AIRA &rarr; AIDEA &rarr;
    MMS/Independent Model Validation &rarr; MCR &rarr; post-prod monitoring) is actually passed. This is the
    target shape Stage&nbsp;3 moves toward, per <code>agents/README.md</code>'s Stage&nbsp;1&rarr;3 staging — not
    a claim that any of it has been deployed, called, or authorised.
  </div>

  <div class="af-stage">
    <div class="af-box af-data future"><h4>1 · Canonical data layer — same code, cloud source</h4>
      <p><code>CanonicalSource</code> + a binding is unchanged (<code>config/bindings/*.yaml</code>). What
      changes is only which binding is active: <code>fdm.yaml</code>/<code>legacy.yaml</code> point at local
      CSVs today; a Snowflake- or Glue/Iceberg/Athena-backed binding would point at the same canonical
      concepts in the bank's warehouse. No detector, agent, or correlation code changes — see
      <code>docs/adding_a_new_domain.md</code>'s extensibility principle.</p>
    </div>

    <div class="af-arrow-down dash"></div>

    <div class="af-row2">
      <div class="af-box future"><h4>AgentCore Gateway</h4>
        <p>Exposes <code>agents/tools.py</code>'s detector-backed tools as MCP tools other AgentCore agents
        or a chat surface could call — instead of being passed straight into a local
        <code>Agent(tools=...)</code> call. <code>mcp</code> is already on the bank's approved package index
        (<code>docs/decision_record.md</code> Tab 7) but unused in this repo today.</p>
      </div>
      <div class="af-box future"><h4>AgentCore Identity</h4>
        <p>Per-tool workload identity / OAuth instead of a local process calling Python functions directly —
        who or what is allowed to invoke <code>check_collateral_coverage</code> on whose behalf becomes an
        access-control decision, not an import.</p>
      </div>
    </div>

    <div class="af-arrow-down dash"></div>

    <div class="af-bus"><div class="line"></div>
      <div class="drop" style="left:12.5%"></div><div class="drop" style="left:37.5%"></div>
      <div class="drop" style="left:62.5%"></div><div class="drop" style="left:87.5%"></div>
    </div>

    <div class="af-row4">
      <div class="af-agent">
        <div class="af-box"><h4>Deposits agent</h4>
          <p>Same <code>agents/domain_agent.py</code> code, same tools. Runs inside one AgentCore Runtime
          container (<code>Dockerfile</code>, <code>agents/entrypoint.py</code>'s
          <code>@app.entrypoint</code>) instead of a local process.</p>
          <span class="af-runtime-chip">narrates via internal Model Gateway, not Ollama</span>
        </div>
      </div>
      <div class="af-agent">
        <div class="af-box"><h4>Lending agent</h4>
          <p>Same code. <code>agents/model_factory.py::get_model()</code>'s <code>mode="model_gateway"</code>
          branch (today a documented <code>NotImplementedError</code>) becomes the live path.</p>
          <span class="af-runtime-chip">narrates via internal Model Gateway, not Ollama</span>
        </div>
      </div>
      <div class="af-agent">
        <div class="af-box"><h4>Risk agent</h4>
          <p>Same code. No detector logic changes to move here — only the model provider and the container
          it runs in.</p>
          <span class="af-runtime-chip">narrates via internal Model Gateway, not Ollama</span>
        </div>
      </div>
      <div class="af-agent">
        <div class="af-box exo"><h4>Exogenous agent</h4>
          <p>Same code. Exposure qualification stays a plain-Python check against the client's own data —
          that never becomes an LLM call, local or cloud.</p>
          <span class="af-runtime-chip">narrates via internal Model Gateway, not Ollama</span>
        </div>
      </div>
    </div>

    <div class="af-converge">
      <div class="rise" style="left:12.5%"></div><div class="rise" style="left:37.5%"></div>
      <div class="rise" style="left:62.5%"></div><div class="rise" style="left:87.5%"></div>
      <div class="line"></div><div class="down"></div>
    </div>

    <div class="af-row2">
      <div class="af-box future"><h4>AgentCore Observability</h4>
        <p>Required by the governance gate's "audit trail" line (Tab 5, Phase 2). Every tool call and
        narration attempt traced (CloudWatch/X-Ray-shaped) — this repo's local equivalent today is
        <code>var/state.sqlite</code>'s run/detection/narrative log, read on the <b>Status</b> page.</p>
      </div>
      <div class="af-box candidate"><h4>Comet / Opik <span class="af-notrun">CANDIDATE</span></h4>
        <p>Suggested for LLM-specific narrative eval — prompt/response logging, drift between model
        versions, hallucination checks alongside the existing evidence-consistency validator
        (<code>agents/domain_agent.py::_validate()</code>). Not on <code>docs/decision_record.md</code> Tab 7's
        verified package list. Would need the Artifactory-sourced/self-hosted route and its own Security
        STaRT/DRA — the hosted SaaS variant would ship narrative text off the bank's network, which CLAUDE.md's
        "no external communication" boundary rules out as-is.</p>
      </div>
    </div>

    <div class="af-arrow-down dash"></div>

    <div class="af-box candidate" style="width:min(760px,92%)"><h4>AgentCore Memory <span class="af-notrun">CANDIDATE</span></h4>
      <p>Optional — evaluate only if a genuine multi-turn RM copilot needs conversational memory across
      sessions. The deterministic Signal Bus + Hypothesis Assembler below already give full traceability
      without it; adding memory before that need is demonstrated would be scope the governance gate has to
      review for nothing.</p>
    </div>

    <div class="af-arrow-down"></div>

    <div class="af-stage af-single">
      <div class="af-box"><h4>Signal Bus <span class="unchanged">UNCHANGED</span></h4>
        <p><code>agents/orchestrator.py::signals_from_tool_evidence()</code> — same file, same tests, runs
        inside the container exactly as it runs locally.</p>
      </div>
      <div class="af-arrow-down"></div>
      <div class="af-box"><h4>Hypothesis Assembler <span class="unchanged">UNCHANGED</span></h4>
        <p><code>datainsights/correlation/hypothesis.py::assemble()</code> — still plain Python, still the
        only place category/hypothesis/sizing is decided. No AWS service ever gets a vote here.</p>
      </div>
      <div class="af-arrow-down dash"></div>
    </div>

    <div class="af-out-row">
      <div class="af-box future"><h4>MIMO insight — real publish</h4>
        <p>Same JSON shape as <code>datainsights/sinks/mimo_placeholder.py</code> produces today. Batch path
        per Tab 4: Snowflake COPY INTO &rarr; S3 staging &rarr; manifest/<code>.tok</code> &rarr; publish
        bucket &rarr; MIMO import.</p>
      </div>
      <div class="af-box future"><h4>Pega event — real publish</h4>
        <p>Same row shape as <code>datainsights/sinks/pega_event_mock.py</code> produces today, landing in
        <code>SFPGMOD004_DS_PEGA_ORGANISATION_EVENT</code> — the insertion point Tab 4 names for an
        NBA-shaped organisation event.</p>
      </div>
      <div class="af-box future"><h4>NBA response feedback</h4>
        <p>Pega&rarr;CRM response taxonomy (Customer Engaged / Not Appropriate / Remind Me Later) joins back
        on <code>recommendation_id</code> — the ML training label D6 names as "blocked on access, not
        data." No propensity model exists to consume it yet.</p>
      </div>
    </div>
  </div>
</div>
"""


