"""Overview page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
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
