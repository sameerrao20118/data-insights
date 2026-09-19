"""Technique reference page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    st.caption("Where each kind of logic sits, and why the rest isn't ML yet.")
    tech_df = pd.DataFrame(TECHNIQUE_ROWS, columns=["Stage", "Technique", "Why"])
    st.dataframe(tech_df, width="stretch", hide_index=True)
    st.divider()
    st.subheader("The six recommendation categories")
    for cat, (desc, color) in CATEGORY_INFO.items():
        st.markdown(f"**{cat.replace('_', ' ').title()}** — {desc}")
