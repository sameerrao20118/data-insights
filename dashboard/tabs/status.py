"""Status page -- split out of dashboard/app.py by R22; retargeted by R23 from the
legacy state database to the agentic run record (var/agent_traces.db)."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401


def render() -> None:
    if not TRACE_DB.exists():
        st.warning("No run record yet — run **Live narration** or **Trace one client** at least once "
                   "(batch worklist runs are deterministic and record no narration traces).")
        return
    con = sqlite3.connect(TRACE_DB)
    try:
        n_traces = con.execute("SELECT COUNT(*) FROM agent_traces").fetchone()[0]
        n_recs = con.execute("SELECT COUNT(DISTINCT recommendation_id) FROM agent_traces").fetchone()[0]
        n_fallback = con.execute("SELECT COUNT(*) FROM agent_traces WHERE fell_back = 1").fetchone()[0]
        last = con.execute("SELECT prty_id, domain, narrative_source, created_at FROM agent_traces "
                           "ORDER BY created_at DESC LIMIT 1").fetchone()
        by_source = con.execute("SELECT narrative_source, COUNT(*) FROM agent_traces GROUP BY narrative_source "
                                "ORDER BY 2 DESC").fetchall()
    finally:
        con.close()
    c1, c2, c3 = st.columns(3)
    c1.metric("Narration traces recorded", n_traces)
    c2.metric("Recommendations narrated", n_recs)
    c3.metric("Template fallbacks", n_fallback)
    if last:
        st.caption(f"Last trace: client `{last[0]}`, domain `{last[1]}`, source `{last[2]}` at {last[3]}")
    if by_source:
        st.dataframe(pd.DataFrame(by_source, columns=["narrative_source", "traces"]), hide_index=True, width="stretch")
    st.caption("A manual/replay POC, not 24/7 monitoring — nothing here auto-refreshes. "
               "Whole-book batch runs write var/insights/*, not traces.")
