"""AWS target architecture page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    st.caption(
        "The SAME diagram as **How it works**, relabelled for where each box goes on AWS per "
        "`docs/decision_record.md`'s D4/Tab 5 (\"AgentCore staging\") and Tab 7 (verified package list). "
        "This is the target shape Stage 3 moves toward — **nothing on this page has been built, deployed, "
        "or called.** See `agents/README.md`'s Stage 1 → 3 table for current status of every piece."
    )
    st.iframe(AGENTCORE_VISION_HTML, height=1780)

    st.divider()
    st.markdown(
        "**Why this page exists:** `CLAUDE.md` requires AWS/Bedrock/AgentCore adapters to stay "
        "contract-and-mock only until explicitly authorised, and `agents/model_factory.py`'s "
        "`mode=\"model_gateway\"` branch is a deliberate `NotImplementedError`, not a stub that quietly "
        "degrades to something that works. This view lets a stakeholder see the intended end state "
        "without implying any of it is running — the local **How it works** page is the one that's actually "
        "verified end to end."
    )
    st.markdown(
        "**Comet/Opik note:** neither appears anywhere else in this repo or in "
        "`docs/decision_record.md`'s Tab 7 verified-package list. It's shown here only because it was "
        "asked about — as a candidate for LLM-specific narrative evaluation (prompt/response logging, "
        "drift, hallucination checks), alongside the AgentCore Observability tracing the governance gate "
        "already requires. Treat it as unvetted: it would need to clear the same Artifactory-sourced, "
        "self-hosted, Security STaRT/DRA path as every other package in Tab 7 before any real narrative "
        "text could reach it."
    )


# ---------- Verification proofs ----------

    st.divider()
    st.subheader("Local machine now vs. Snowflake later")
    st.caption("Where each layer runs today, and what changes when a real warehouse is wired in. "
               "The point of the row below: detector/ranking/narrative code is unchanged.")
    compare_df = pd.DataFrame([
        ("Source", "DuckDB over CSV", "SnowflakeSource, same DataSource interface"),
        ("Detector / ranking / narrative code", "Unchanged", "Unchanged - that's the point"),
        ("External events", "Simulated CSV", "External Access Integration -> real ECB/TED/GDELT APIs, server-side"),
        ("State", "SQLite", "Same, or a remote store if concurrency needs it"),
        ("LLM", "Local Ollama", "Local Ollama - never Cortex/Bedrock, by policy"),
    ], columns=["", "Today (local)", "Later (Snowflake)"])
    st.dataframe(compare_df, width="stretch", hide_index=True)
    st.caption("Full comparison: `docs/artifacts/deployment-options.html` \u00b7 setup steps: "
               "`docs/snowflake_setup.md`")
