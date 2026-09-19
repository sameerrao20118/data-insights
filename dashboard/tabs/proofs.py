"""Verification proofs page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    st.caption(
        "Four things built into this platform so it isn't locked to one bank's schema, one "
        "industry sector, one external event type, or a plausible-sounding-but-wrong AI answer. "
        "Each proof below runs for real, against real data — not a slide."
    )

    st.subheader("1 · Same detectors, a second real schema")
    st.markdown(
        "The 9 deterministic detectors above never touch a physical column name — they read "
        "through `CanonicalSource` + a binding (`config/bindings/fdm.yaml`). "
        "`config/bindings/legacy.yaml` maps a *structurally different* schema (this repo's older "
        "pipeline, `data_generator/output/`) onto the same canonical concepts — proving a new bank "
        "data model is a YAML mapping, not an engineering rewrite."
    )
    if st.button("▶ Run: same detectors against the second schema", key="run_legacy_proof"):
        run_module(["pytest", "tests/test_legacy_binding_end_to_end.py", "-q"], "Schema portability proof")
    entry = next((e for e in st.session_state.get("logs", []) if e["label"] == "Schema portability proof"), None)
    if entry:
        if entry["ok"]:
            st.success("5/5 passed — deposits/lending detectors fire real signals against the legacy "
                      "schema; risk detectors correctly report `not_available_under_this_binding` "
                      "(that data isn't contracted there) instead of crashing.")
        else:
            st.error("Failed — see log.")
        with st.expander("Raw test output"):
            st.code(entry["out"] or entry["err"], language="text")

    st.divider()
    st.subheader("1b · A third schema — real commercial entities, not synthetic")
    st.markdown(
        "`config/bindings/sba.yaml` maps REAL U.S. Small Business Administration PPP loan data "
        "(real borrower names, real NAICS industry sectors, real loan amounts/dates/outcomes — "
        "`data_generator/fdm/load_sba.py`) onto the same canonical concepts. Deposit-account "
        "activity is synthetically layered on top of these real entities, disclosed as such — no "
        "public source discloses real transaction history for any commercial client, a "
        "confidentiality constraint on the whole data category, not a gap in this build. Sector "
        "spread is real, not invented: 24 distinct NAICS industries in the sampled book."
    )
    if st.button("▶ Run: same detectors against real commercial entities", key="run_sba_proof"):
        run_module(["pytest", "tests/test_sba_binding_end_to_end.py", "-q"], "Real-data schema proof")
    entry = next((e for e in st.session_state.get("logs", []) if e["label"] == "Real-data schema proof"), None)
    if entry:
        if entry["ok"]:
            st.success("6/6 passed — real sector diversity confirmed (≥10 distinct NAICS codes); "
                      "deposits/lending detectors fire real signals; risk detectors correctly "
                      "report unavailability (PPP loans have no continuous rating history and "
                      "aren't collateralised) instead of crashing.")
        else:
            st.error("Failed — see log.")
        with st.expander("Raw test output"):
            st.code(entry["out"] or entry["err"], language="text")

    st.divider()
    st.subheader("2 · External event types are config, not code")
    try:
        from external_events import event_registry
        reg = event_registry.load_registry()
        reg_rows = [{"event_type": name, "description": s.description,
                    "real_source": s.source.get("real_source_type", "")} for name, s in reg.items()]
        st.dataframe(pd.DataFrame(reg_rows), width="stretch", hide_index=True)
    except Exception as e:  # noqa: BLE001
        st.caption(f"Registry unavailable: {e}")
    st.markdown(
        "`fx_rate_move` was added to `config/event_types.yaml` with **zero changes** to "
        "`external_events/exposure_qualifier.py`, `datainsights/correlation/hypothesis.py`, or the "
        "AI extraction agent."
    )
    if st.button("▶ Run: second event type qualifies for real", key="run_fx_proof"):
        run_module(["pytest", "tests/test_fx_exposure_end_to_end.py", "-q"], "FX event-type proof")
    entry = next((e for e in st.session_state.get("logs", []) if e["label"] == "FX event-type proof"), None)
    if entry:
        if entry["ok"]:
            st.success("3/3 passed — a client with real (test-injected) USD activity qualifies for the "
                      "FX-move event; a client without it correctly does not.")
        else:
            st.error("Failed — see log.")
        with st.expander("Raw test output"):
            st.code(entry["out"] or entry["err"], language="text")

    st.divider()
    st.subheader("3 · The AI's own proposal gets checked, not trusted")
    st.markdown(
        "The A2 investigator agent (`agents/investigator_agent.py`) resolves an ambiguous signal "
        "(e.g. `fixed_rate_expiry` → TREASURY_OPPORTUNITY or HEDGING_NEED) by asking a local LLM — "
        "but every proposal is recomputed in Python against the client's own data before "
        "acceptance. A live run repeatedly proposed HEDGING_NEED for a client with **zero** foreign-"
        "currency activity; this check catches that, live, below."
    )
    if st.button("▶ Run: live evidence-consistency check (real Ollama)", key="run_a2_proof"):
        run_module(["pytest", "tests/test_investigator_agent.py::test_live_investigation_proposes_a_valid_category",
                   "-q", "-s"], "A2 evidence-check proof")
    entry = next((e for e in st.session_state.get("logs", []) if e["label"] == "A2 evidence-check proof"), None)
    if entry:
        if entry["ok"]:
            st.success(
                "Passed — either the AI's proposal matched its own evidence, or the mismatch was "
                "caught and downgraded to `needs_review` rather than shown to the RM as fact.")
        else:
            st.error("Failed — see log.")
        with st.expander("Raw test output (includes the model's actual proposal + reasoning)"):
            st.code(entry["out"] or entry["err"], language="text")
