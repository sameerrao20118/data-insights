"""
DataInsights demo dashboard (Streamlit) -- entry point.

Run: streamlit run dashboard/app.py

R22 (docs/refactor_plan.md §6k): this file is the sidebar and the page
dispatch only. Shared helpers, paths and diagrams: dashboard/common.py.
One module per page: dashboard/tabs/<page>.py, each exposing render().
tests/test_dashboard_pages_render.py renders every page under
streamlit's AppTest in the suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.common import *  # noqa: E402,F401,F403 -- runs st.set_page_config first
from dashboard.tabs import PAGES  # noqa: E402

# ---------- sidebar ----------

with st.sidebar:
    st.markdown("### DataInsights")
    st.caption("Commercial/institutional NBA-EBM proof of concept")
    ok = ollama_reachable()
    st.markdown(f"**Local Ollama:** {'🟢 reachable' if ok else '🔴 not reachable'}")
    if not ok:
        st.caption("Narrative steps fall back to a deterministic template, per project policy — no cloud fallback ever.")
    try:
        from datainsights.identity import resolve_principal
        from datainsights.runtime import active_profile
        st.caption(f"Signed in: {resolve_principal(active_profile()).describe()}")
    except Exception as _e:  # noqa: BLE001 -- identity problems are shown, never hidden
        st.caption(f"Identity: {type(_e).__name__}: {_e}")
    st.divider()
    page = st.radio(
        "Section",
        list(PAGES),
        label_visibility="collapsed",
    )
    st.divider()
    st.caption(
        "Synthetic data only — no real client, transaction, or event data "
        "anywhere in this repo. This dashboard is a demo viewer, not the "
        "review/tracking system named as a future gap in docs/gap_analysis.md."
    )


st.title(page if page != "Overview" else "DataInsights — how this works")

PAGES[page]()
