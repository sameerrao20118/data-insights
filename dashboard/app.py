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
from dashboard.tabs import PAGE_GROUPS, PAGES  # noqa: E402

# ---------- sidebar ----------
# Navigation + status only, per the Streamlit layout guidance: main content
# belongs in the main area. Pages are grouped (dashboard/tabs/__init__.py's
# PAGE_GROUPS) so the entry point is obvious instead of being the first of
# ten equal-looking peers.

with st.sidebar:
    st.markdown("### DataInsights")
    st.caption("Commercial/institutional NBA-EBM")

    ok = ollama_reachable()
    st.badge("Ollama reachable" if ok else "Ollama unreachable",
             icon=":material/check_circle:" if ok else ":material/error:",
             color="green" if ok else "orange")
    if not ok:
        st.caption("Narration falls back to a deterministic template — never a cloud call.")

    try:
        from datainsights.identity import resolve_principal
        from datainsights.runtime import active_profile
        _principal = resolve_principal(active_profile())
        st.caption(f":material/person: {_principal.describe()}")
    except Exception as _e:  # noqa: BLE001 -- identity problems are shown, never hidden
        st.caption(f":material/person_off: Identity: {type(_e).__name__}: {_e}")

    st.divider()

    # ONE radio over all pages, ordered by group, with the group name
    # prefixed onto each label. A radio per group would put the heading
    # closer to its pages, but needs four widgets coordinating through
    # session state to keep a single selection -- more moving parts than
    # the grouping is worth, and it breaks the single-widget assumption
    # tests/test_dashboard_pages_render.py drives navigation with.
    # A heading prefixed onto the first option's label rendered as part of
    # that option -- the group name sat on the radio row and read like a
    # selectable item. Streamlit has no option-group primitive for a radio,
    # so the working alternative is a selectbox per group plus one "active
    # group" marker: exactly one group holds the live selection, and the
    # heading is a real label above its own control rather than text glued
    # to a row.
    if "nav_page" not in st.session_state:
        st.session_state["nav_page"] = "Explore a source"
        st.session_state["nav_group"] = PAGE_GROUPS[0][0]

    for _label, _names in PAGE_GROUPS:
        _is_active = st.session_state["nav_group"] == _label
        _chosen = st.selectbox(
            _label, _names, key=f"nav_sel_{_label}",
            index=_names.index(st.session_state["nav_page"]) if _is_active else None,
            placeholder="—", label_visibility="visible",
        )
        # A group only claims the page when its own selection CHANGED, so
        # re-rendering the active group does not steal focus back.
        if _chosen and (_chosen != st.session_state["nav_page"]):
            st.session_state["nav_page"] = _chosen
            st.session_state["nav_group"] = _label

    page = st.session_state["nav_page"]

    st.divider()
    st.caption(
        "Synthetic data only — no real client, transaction, or event data "
        "anywhere in this repo. This dashboard is a demo viewer, not the "
        "review/tracking system named as a future gap in docs/gap_analysis.md."
    )


st.title(page if page != "Overview" else "DataInsights — how this works")

PAGES[page]()
