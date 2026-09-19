"""Onboard a source page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    st.caption(
        "Add a new data asset without touching a single line of existing detector, agent or "
        "correlation code. Three steps: profile + propose, review, accept. **Purely additive** - "
        "nothing here writes to `config/` until you explicitly click Accept in step 3, and even "
        "then only for the schema name you type. Every existing source stays untouched. Full "
        "walkthrough: `onboarding/README.md`."
    )

    st.markdown("**1 \u00b7 Profile + propose**")
    st.caption(
        "Point at a directory of CSV files. A deterministic profiler reads their "
        "structure (columns, types, key candidates, bi-temporal pairs); a local "
        "Ollama call then proposes which canonical concept each table maps to, with "
        "a confidence and evidence per mapping - nothing is registered yet."
    )
    onb_col1, onb_col2 = st.columns(2)
    with onb_col1:
        onb_dir = st.text_input("Data directory", value="data_generator/output_fdm_sba")
    with onb_col2:
        onb_name = st.text_input("Schema name", value="my_new_schema")

    if st.button("Profile this directory and propose a binding (live Ollama)", width="stretch"):
        run_module(["onboarding.propose", onb_dir, "--name", onb_name], f"Onboard propose - {onb_name}")

    propose_entry = next((e for e in st.session_state.get("logs", [])
                         if e["label"] == f"Onboard propose - {onb_name}"), None)
    review_path = ROOT / "onboarding" / "proposals" / onb_name / "review.md"
    binding_path = ROOT / "onboarding" / "proposals" / onb_name / "binding.proposed.yaml"

    if propose_entry and not propose_entry["ok"]:
        st.error("Proposal failed - see log.")
        with st.expander("Raw output"):
            st.code(propose_entry["out"] or propose_entry["err"], language="text")
    elif review_path.exists():
        st.success(f"Proposal written to `onboarding/proposals/{onb_name}/`.")
        with open(review_path) as f:
            st.markdown(f.read())
        st.divider()
        st.markdown("**2 \u00b7 Review the proposed YAML**")
        st.caption(
            "Every mapping above came from the model - review it here. `derived:` "
            "(value maps, constants) and `joins:` are never proposed automatically; "
            "add them by hand in this box if a concept needs one, same as every "
            "binding already in `config/bindings/`."
        )
        with open(binding_path) as f:
            proposed_yaml = f.read()
        edited_yaml = st.text_area("binding.proposed.yaml", value=proposed_yaml, height=320)
        if st.button("Save edits back to the proposal file"):
            with open(binding_path, "w") as f:
                f.write(edited_yaml)
            st.success("Saved.")

        st.divider()
        st.markdown("**3 \u00b7 Accept (the only step that writes to config/)**")
        st.caption(
            "Validates the (possibly hand-edited) proposal against its generated "
            "contract - the same `validate_binding()` check every hand-written "
            "binding is held to - then copies it into `config/bindings/` and "
            "`config/entities_<name>.yaml`. Refuses to overwrite an existing schema "
            "of the same name unless you tick the box below."
        )
        force_overwrite = st.checkbox(f"Overwrite an existing `{onb_name}` binding if one exists")
        if st.button("Accept and register this schema", width="stretch"):
            args = ["onboarding.accept", onb_name] + (["--force"] if force_overwrite else [])
            run_module(args, f"Onboard accept - {onb_name}")
        accept_entry = next((e for e in st.session_state.get("logs", [])
                            if e["label"] == f"Onboard accept - {onb_name}"), None)
        if accept_entry:
            if accept_entry["ok"]:
                st.success(f"Registered. `config/bindings/{onb_name}.yaml` and "
                          f"`config/entities_{onb_name}.yaml` are now real. Point a profile's "
                          f"`source.binding` at {onb_name!r} to use it, then find it in "
                          f"**Explore a source** once a profile exists for it.")
            else:
                st.error("Accept failed - validation caught a problem, nothing was written.")
            with st.expander("Raw output"):
                st.code(accept_entry["out"] or accept_entry["err"], language="text")
    else:
        st.info("No proposal yet for this schema name - click the button above.")
