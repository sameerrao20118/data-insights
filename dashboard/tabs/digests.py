"""Digests page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    digest_files = sorted(INSIGHTS_DIR.glob("*digest*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if INSIGHTS_DIR.exists() else []
    if not digest_files:
        st.warning("No digest yet — run a pipeline from the **Explore a source** tab.")
    else:
        names = [p.name for p in digest_files]
        chosen = st.selectbox("Digest file", names)
        content = (INSIGHTS_DIR / chosen).read_text()
        st.markdown(content)
