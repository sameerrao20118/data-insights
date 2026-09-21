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
    _group_of = {name: label for label, names in PAGE_GROUPS for name in names}
    _ordered = [name for _, names in PAGE_GROUPS for name in names]

    def _nav_label(name: str) -> str:
        """First page of each group carries the group heading."""
        label = _group_of[name]
        first_in_group = next(n for n in _ordered if _group_of[n] == label)
        return f"{label.upper()}\n\n{name}" if name == first_in_group else name

    page = st.radio("Section", _ordered, label_visibility="collapsed",
                    format_func=_nav_label)

    st.divider()
    st.caption(
        "Synthetic data only — no real client, transaction, or event data "
        "anywhere in this repo. This dashboard is a demo viewer, not the "
        "review/tracking system named as a future gap in docs/gap_analysis.md."
    )


st.title(page if page != "Overview" else "DataInsights — how this works")

PAGES[page]()
