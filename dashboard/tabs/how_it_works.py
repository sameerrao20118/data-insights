"""How it works page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    st.caption(
        "How `agents/orchestrator.py::evaluate_client()` — the one function AgentCore Runtime, a "
        "batch job, and every demo script here all call — actually wires the domain agents "
        "together for ONE client."
    )
    st.iframe(AGENT_FLOW_HTML, height=1520)

    st.divider()
    st.markdown(
        "**Why the LLM boxes are dashed:** every domain agent's narration is independently "
        "validated against its own tool evidence (`agents/domain_agent.py`'s validate-or-fallback "
        "discipline) and falls back to a deterministic template on any failure — but even a "
        "*successful* narration never reaches the Signal Bus or the Assembler. Category, sizing, "
        "and the RM's revenue figure are 100% reproducible without Ollama running at all; "
        "`python -m agents.demo_fdm_scenario` (batch mode, no LLM) and "
        "`python -m agents.demo_multiagent_scenario` (live LLM) produce the identical "
        "`Recommendation` for the same client — that equivalence is what "
        "`tests/test_orchestrator.py` asserts."
    )
