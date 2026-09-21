"""Explore a source page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

# ---------- per-source renderers for "Explore a source" ----------
# One dispatcher tab, one selector for WHICH input is loaded; what you can
# DO differs by source and is rendered here, not by a separate tab per
# source/phase.

# Ground truth. NEVER listed, never previewed, never counted. OfflineLocalSource
# refuses these paths at the DataSource layer, but this browser reads CSVs
# directly with pandas and would bypass that guard -- so the boundary is
# re-enforced here, at the only other place that touches these files.
PROTECTED_MARKER = "protected_evaluator_only"


def _source_tables(data_root: Path) -> list[dict]:
    """Every CSV under a source root, recursively, with the protected
    evaluator directory hard-excluded."""
    if not data_root or not data_root.exists():
        return []
    rows = []
    for path in sorted(data_root.rglob("*.csv")):
        if PROTECTED_MARKER in path.parts:
            continue
        n = count_rows(path)
        head = read_csv(path, nrows=1)
        rows.append({
            "table": path.stem,
            "folder": str(path.parent.relative_to(data_root)) or ".",
            "rows": n if n is not None else 0,
            "columns": len(head.columns) if head is not None else 0,
            "path": path,
        })
    return rows


def _render_source_data_panel(source_key: str, src: dict) -> None:
    """Answers two questions the UI previously could not: where IS the data
    for this source, and is it one table or several."""
    tables = _source_tables(src.get("data_root"))
    if not tables:
        st.warning("No data found for this source yet - generate it first, e.g. "
                   "`python -m data_generator.fdm.generate_fdm --seed 42`.")
        return

    total_rows = sum(t["rows"] for t in tables)
    title = (f"Data behind this source - {len(tables)} tables, {total_rows:,} rows "
             f"(click to browse)")
    with st.expander(title):
        st.caption(
            f"Physical CSVs under `{src['data_root'].relative_to(ROOT)}`. This is the raw input "
            f"the detectors read through the semantic binding - not a curated view. "
            f"Ground-truth label files are excluded by code, never listed here."
        )
        inventory = pd.DataFrame([{k: t[k] for k in ("table", "folder", "rows", "columns")}
                                  for t in tables])
        st.dataframe(inventory, width="stretch", hide_index=True)

        pick = st.selectbox("Preview a table", [t["table"] for t in tables],
                            key=f"data_preview_{source_key}")
        chosen = next(t for t in tables if t["table"] == pick)
        preview = read_csv(chosen["path"], nrows=25)
        if preview is not None:
            st.dataframe(preview, width="stretch", hide_index=True)
            st.caption(f"First 25 of {chosen['rows']:,} rows - "
                       f"{', '.join(str(c) for c in preview.columns)}")

        st.markdown("**How these tables become canonical concepts**")
        try:
            from datainsights.semantic.binding import load_binding

            binding = load_binding(source_key)
            concept_rows = []
            for concept, cb in binding.concepts.items():
                if cb.unavailable:
                    concept_rows.append({"canonical concept": concept, "available": "no",
                                         "physical table(s)": "-", "why not": cb.unavailable})
                else:
                    names = [cb.entity] + [j.entity for j in cb.joins]
                    concept_rows.append({"canonical concept": concept, "available": "yes",
                                         "physical table(s)": " + ".join(n for n in names if n),
                                         "why not": ""})
            st.dataframe(pd.DataFrame(concept_rows), width="stretch", hide_index=True)
            st.caption(
                f"`config/bindings/{source_key}.yaml` maps these physical tables onto the "
                f"canonical concepts in `config/semantic_model.yaml`. A concept marked "
                f"**no** is genuinely absent from this schema - the detectors that need it "
                f"report unavailable rather than crash or guess."
            )
        except Exception as e:  # noqa: BLE001 -- a missing binding must not break the browser
            st.caption(f"No binding loaded for {source_key!r}: {type(e).__name__}: {e}")

        st.markdown("**Which recommendation categories this data can produce**")
        try:
            import yaml as _yaml

            with open(ROOT / "config" / "domains_fdm.yaml") as f:
                domains_cfg = _yaml.safe_load(f)
            sig_rows = []
            for domain, block in domains_cfg.items():
                for sig, spec in (block.get("signals") or {}).items():
                    sig_rows.append({"domain": domain, "signal": sig,
                                     "category": spec.get("category", "")})
            st.dataframe(pd.DataFrame(sig_rows), width="stretch", hide_index=True)
            mapped = {r["category"] for r in sig_rows}
            unreachable = [c for c in CATEGORY_INFO if c not in mapped]
            if unreachable:
                st.warning(
                    "Defined but **not reachable from any detector today**: "
                    + ", ".join(f"`{c}`" for c in unreachable)
                    + ". A category with no signal behind it can never appear on a worklist - "
                      "adding one means adding a detector and an honest sizing basis, not just "
                      "a label."
                )
        except Exception as e:  # noqa: BLE001
            st.caption(f"Could not read the domain registry: {type(e).__name__}: {e}")


def _render_exogenous_panel() -> None:
    """Exogenous events are NOT per-source -- the same feed is checked against
    whichever client book is loaded -- so this sits ALONGSIDE the per-source
    data browser rather than inside it."""
    ext_path = EXT_DIR / "output_fdm" / "tender_events.csv"  # R23: the declarative event feed (config/event_types.yaml)
    n_ext = count_rows(ext_path)
    label = (f"Exogenous event feed - {n_ext:,} simulated events (shared across all sources)"
             if n_ext is not None else "Exogenous event feed - not generated yet")
    with st.expander(label):
        st.caption(
            "Market/political/industry events. Simulated today "
            "(`SimulatedExternalEventSource`); the interface is designed so a real adapter for "
            "any row below drops in later without touching detector code. This feed is checked "
            "against whichever source you picked above - and an event only ever reaches a "
            "client when that client's OWN data confirms exposure."
        )
        map_df = pd.DataFrame(EXTERNAL_SOURCE_MAP,
                              columns=["Event type", "Real free API it maps to",
                                       "What that source actually brings"])
        st.dataframe(map_df, width="stretch", hide_index=True)
        df_ext = read_csv(ext_path, nrows=15)
        if df_ext is not None:
            st.caption("Sample of simulated events:")
            st.dataframe(df_ext, width="stretch", hide_index=True)
        else:
            st.warning("Not generated yet - run "
                       "`python -m data_generator.fdm.generate_fdm_events`.")


def _render_fdm_worklist_tab(key: str, src: dict) -> None:
    profile = src["profile"]
    generated = INSIGHTS_DIR / "fdm_rm_worklist.csv"
    per_profile = worklist_path_for(profile)

    rc1, rc2 = st.columns([1, 3])
    with rc1:
        if st.button("▶ Run whole-book pipeline", width="stretch", key=f"run_book_{key}"):
            run_module(["agents.demo_fdm_scenario", "--profile", profile],
                       f"Whole-book pipeline ({profile})")
            # Every local profile shares var/insights and the generator
            # always writes fdm_rm_worklist.csv, so two profiles overwrote
            # each other. Snapshot to a per-profile name immediately after
            # the run, so each source's tab shows ITS book.
            if generated.exists():
                import shutil

                shutil.copyfile(generated, per_profile)

    if not per_profile.exists():
        st.warning(
            f"No worklist for `{profile}` yet — click **Run whole-book pipeline** above, or run "
            f"`python -m agents.demo_fdm_scenario --profile {profile}` "
            f"(writes `var/insights/fdm_rm_worklist.csv`)."
        )
        return

    df = read_csv(per_profile)
    # "illustrative" is a disclosure, not explanation -- it stays visible.
    st.caption(f"{len(df):,} recommendations · `{per_profile.relative_to(ROOT)}` · "
               f"revenue figures are **illustrative** planning assumptions, not pricing.")
    revenue = df["indicative_revenue_eur"].dropna()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Recommendations", len(df))
    m2.metric("Revenue-earning", int((~df["nba_category"].isin(["RISK_REVIEW", "ADVISORY_ONLY"])).sum()))
    m3.metric("With a sized offer", int(revenue.shape[0]))
    _by_cur = df.loc[revenue.index].groupby("currency")["indicative_revenue_eur"].sum() if "currency" in df.columns else {"EUR": revenue.sum()}
    m4.metric("Indicative revenue (illustrative)", " · ".join(f"{c} {v:,.0f}" for c, v in dict(_by_cur).items()) or "—")
    st.bar_chart(df["nba_category"].value_counts())

    # "Show me MY clients" is the first thing a relationship manager does
    # with a worklist -- so the RM filter comes first, before the
    # book-wide slicers. Without it the entitlement field added in M17
    # exists in the CSV but is unreachable from the UI.
    # R21: entitlement is not a dropdown. The principal comes from the
    # profile's identity provider (local_dev here, the bank's IdP at
    # Stage 3) and the frame is scoped server-side BEFORE any slicer.
    from datainsights.identity import resolve_principal, scope_worklist
    from datainsights.runtime import active_profile
    principal = resolve_principal(active_profile())
    unscoped_n = len(df)
    df = scope_worklist(df, principal)
    st.caption(f":material/lock: {len(df)} of {unscoped_n} recommendations are in your entitlement "
               f"({principal.describe()}).")
    if df.empty:
        st.info("Nothing in your entitlement on this book.")
        return
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        cats = st.multiselect("Category", sorted(df["nba_category"].unique()), key="fdm_cat")
    with f2:
        segs = st.multiselect("Segment", sorted(df["segment"].dropna().unique()), key="fdm_seg")
    with f3:
        secs = st.multiselect("Sector", sorted(df["sector"].dropna().unique()), key="fdm_sec")
    with f4:
        ctys = st.multiselect("Country", sorted(df["country"].dropna().unique()), key="fdm_cty")
    filtered = df.copy()
    for column, chosen in (("nba_category", cats),
                           ("segment", segs), ("sector", secs), ("country", ctys)):
        if chosen and column in filtered.columns:
            filtered = filtered[filtered[column].isin(chosen)]

    # Click a row to read its card. The dropdown below used to be the only
    # way to choose, which meant selecting twice: once by eye in the table,
    # again in a widget under it. Row selection keeps the dropdown as a
    # fallback so the page still works if no row is selected.
    pool = filtered if len(filtered) else df
    shown = [c for c in ["rank", "prty_id", "nba_category", "signal_strength",
                         "indicative_revenue_eur", "why_now"] if c in pool.columns]
    event = st.dataframe(
        pool[shown], width="stretch", hide_index=True, height=360,
        on_select="rerun", selection_mode="single-row", key=f"worklist_{key}",
        column_config={
            "rank": st.column_config.NumberColumn("#", width="small", pinned=True),
            "prty_id": st.column_config.TextColumn("Client", pinned=True),
            "nba_category": st.column_config.TextColumn("Category"),
            "signal_strength": st.column_config.ProgressColumn(
                "Confidence", min_value=0, max_value=5, format="%d/5"),
            "indicative_revenue_eur": st.column_config.NumberColumn(
                "Revenue (illustrative)", format="%.0f"),
            "why_now": st.column_config.TextColumn("Why now", width="large"),
        },
    )

    st.divider()
    st.subheader("Prepare for the client call")
    labels = {r["rank"]: f"#{r['rank']} — {r['prty_id']} — {r['nba_category']}"
              for _, r in pool.iterrows()}
    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows:
        chosen_rank = int(pool.iloc[selected_rows[0]]["rank"])
        st.caption(":material/ads_click: Showing the row you selected — "
                   "click another, or use the dropdown.")
    else:
        chosen_rank = st.selectbox("Recommendation", list(labels),
                                   format_func=lambda r: labels[r], key=f"rec_pick_{key}")
    row = df[df["rank"] == chosen_rank].iloc[0]
    revenue_text = (f"~EUR {row['indicative_revenue_eur']:,.0f} (illustrative)"
                    if pd.notna(row["indicative_revenue_eur"]) else "none — not a sized revenue opportunity")
    _revenue_category = row["nba_category"] not in ("RISK_REVIEW", "ADVISORY_ONLY")
    st.badge(row["nba_category"].replace("_", " ").title(),
             icon=":material/trending_up:" if _revenue_category else ":material/shield:",
             color="green" if _revenue_category else "gray")
    st.markdown(f"**{row['prty_id']}** — {row['segment']} · {row['sector']} · {row['country']}")
    st.markdown(f"**Why now:** {row['why_now']}")
    st.markdown(f"**Hypothesis:** {row['hypothesis']}")
    st.markdown(f"**Recommended action:** {row['recommended_action']}")
    st.markdown(f"**Indicative revenue to bank:** {revenue_text} — {row['revenue_mechanism']}")
    st.info(f"**Suggested opening:** {row['talking_point']}")
    st.caption(
        f"Confidence {row['signal_strength']}/5, confirmed by: {row['confirming_domains']} · "
        f"Evidence `{row['evidence_ref']}` · Sizing basis `{row['sizing_basis']}` · "
        f"RM response options: {row['response_actions']}"
    )
    if bool(row.get("ambiguous")):
        st.caption("⚠️ This category came from a disclosed simplification "
                  "(see `docs/adding_a_new_domain.md`'s ambiguous-signal note) -- "
                  "worth a second look before leading a client conversation with it.")

    st.divider()
    st.subheader("Ask about this recommendation")
    st.caption(
        "Answers ONLY from this row's own facts — local Ollama, validated, "
        "no other client's data reachable (agents/rm_copilot_agent.py). "
        "Falls back to a plain fact list if the model is unavailable or its "
        "answer doesn't check out."
    )
    rec_id = row.get("recommendation_id", "") or ""
    question = st.text_input("Question", key=f"copilot_q_{chosen_rank}",
                             placeholder="e.g. Why is this sized at that amount?")
    if st.button("Ask", key=f"copilot_ask_{chosen_rank}") and question.strip():
        with st.spinner("Asking (local Ollama)..."):
            try:
                from agents.model_factory import get_model
                from agents.rm_copilot_agent import ask
                from datainsights.runtime import build_runtime

                model = get_model(build_runtime(profile).model_config)
                answer = ask(question, row.to_dict(), model)
                st.session_state[f"copilot_answer_{chosen_rank}"] = answer
            except Exception as e:  # noqa: BLE001 -- dashboard must never crash on a copilot failure
                st.session_state[f"copilot_answer_{chosen_rank}"] = None
                st.error(f"Copilot unavailable: {type(e).__name__}: {e}")
    cached_answer = st.session_state.get(f"copilot_answer_{chosen_rank}")
    if cached_answer is not None:
        if cached_answer.status == "fallback":
            st.warning(cached_answer.answer)
        else:
            st.markdown(f"**Answer:** {cached_answer.answer}")
        st.caption(f"_source: {cached_answer.narrative_source}_")

    st.divider()
    st.subheader("Record RM response")
    st.caption(
        "The live Pega->CRM taxonomy, captured here against this recommendation's "
        "stable id — this is the actual start of the feedback loop D6 describes "
        "(datainsights/rm_feedback.py); no propensity model reads it yet."
    )
    if not rec_id:
        st.caption("(this row has no recommendation_id — regenerate the worklist to get one)")
    else:
        from datainsights.rm_feedback import RESPONSE_ACTIONS, connect, feedback_for_recommendation, record_feedback

        feedback_db_path = str(ROOT / "var" / "rm_feedback.db")
        response = st.radio("Response", RESPONSE_ACTIONS, key=f"fb_response_{chosen_rank}", horizontal=True)
        sub_reason = st.text_input("Sub-reason (optional)", key=f"fb_reason_{chosen_rank}")
        if st.button("Save response", key=f"fb_save_{chosen_rank}"):
            with connect(feedback_db_path) as con:
                record_feedback(con, recommendation_id=rec_id, prty_id=row["prty_id"],
                                response=response, sub_reason=sub_reason, recorded_by="dashboard")
            st.success(f"Recorded: {response}")

        with connect(feedback_db_path) as con:
            history = feedback_for_recommendation(con, rec_id)
        if history:
            st.caption("Response history for this recommendation:")
            st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True)


def _render_fdm_live_demo_tab(key: str, src: dict) -> None:
    """Structured, not a stdout dump: runs the SAME evaluate_client() the
    Trace tab runs, with narrate=True, and renders each agent's validated
    narrative as its own card."""
    from datetime import timedelta

    profile = src["profile"]
    st.caption("Same pipeline as Trace, with narration on. Slower (~15-25s). "
               "**The recommendation is identical either way** — the LLM narrates, it never "
               "decides the category, sizing or score.")
    with st.expander("How narration is kept honest", icon=":material/info:"):
        st.markdown(
            "Each domain agent makes a real local Ollama call to write its observed facts, "
            "hypothesis and suggested action. Every narrative is **validated against that "
            "agent's own tool evidence** before being accepted — schema, numeric "
            "traceability, direction, currency and banned-term checks — and falls back to a "
            "deterministic template on any failure. An unvalidated claim never reaches an RM."
        )
    try:
        party_ids = _client_ids(profile, src["picker_concept"])
    except Exception as e:  # noqa: BLE001 -- an unreadable source is reported, never a crash
        st.warning(f"Cannot list clients for `{profile}`: {type(e).__name__}: {e}")
        return
    if not party_ids:
        st.warning(f"No clients found for `{profile}` — has its data been generated?")
        return

    default_idx = party_ids.index("PRTY00036") if "PRTY00036" in party_ids else 0
    c1, c2 = st.columns([3, 1])
    with c1:
        customer = st.selectbox("Client", party_ids, index=default_idx,
                                key=f"narrate_client_{key}")
    with c2:
        st.markdown("&nbsp;")
        go = st.button("Run live narration", width="stretch", key=f"narrate_go_{key}")

    if go:
        if not ollama_reachable():
            st.error("Local Ollama is not reachable - every agent would fall back to its "
                     "deterministic template. That is the CORRECT behaviour, but it makes this "
                     "a poor demonstration of narration. Start Ollama and try again.")
            return
        with st.spinner("Domain agents calling local Ollama (validated before acceptance)..."):
            try:
                from agents.model_factory import get_model
                from agents.orchestrator import evaluate_client
                from datainsights.runtime import build_runtime
                from external_events.exposure_qualifier import load_events

                rt = build_runtime(profile)
                event = load_events(rt.event_source_path)[0] if rt.event_source_path else None
                as_of = (event.event_date + timedelta(days=90)) if event else None
                evaluation = evaluate_client(
                    customer, source=rt.source, rules=rt.rules, as_of=as_of, event=event,
                    model=get_model(rt.model_config), narrate=True,
                    binding_name=rt.binding_name)
                st.session_state[f"narrate_result_{key}"] = (customer, evaluation)
            except Exception as e:  # noqa: BLE001 -- never kill the tab on a model failure
                st.session_state.pop(f"narrate_result_{key}", None)
                st.error(f"Live narration failed: {type(e).__name__}: {e}")
                return

    cached = st.session_state.get(f"narrate_result_{key}")
    if not cached or cached[0] != customer:
        st.info("Pick a client and click **Run live narration**. Nothing needs to have been run "
                "first. Use **Trace one client** for the fast, no-LLM view of the same pipeline.")
        return
    _, evaluation = cached

    st.markdown("#### Each agent's narrative - written by the LLM, validated before acceptance")
    for domain, result in evaluation.agent_results.items():
        if isinstance(result, dict):
            continue
        with st.container(border=True):
            head1, head2 = st.columns([3, 2])
            with head1:
                st.markdown(f"**{domain} agent**")
            with head2:
                if result.fell_back:
                    st.markdown("**deterministic template** (LLM output rejected or unavailable)")
                else:
                    st.markdown(f"narrated by `{result.narrative_source}`")
            fired = [t for t, o in (result.tool_evidence or {}).items()
                     if isinstance(o, dict) and o.get("status") == "detected"]
            meta = f"Tools that fired: {', '.join(f'`{t}`' for t in fired) if fired else 'none'}"
            meta += f" - {result.latency_seconds:.1f}s"
            if result.prompt_version:
                meta += f" - prompt v{result.prompt_version}"
            st.caption(meta)
            st.markdown(f"**Observed facts:** {result.observed_facts}")
            st.markdown(f"**Hypothesis:** {result.hypothesis}")
            st.markdown(f"**Suggested action:** {result.suggested_action}")
            if result.caveats:
                st.caption(f"Caveats: {result.caveats}")

    rec = evaluation.recommendation
    st.markdown("#### The deterministic assembler's call - unchanged by any of the above")
    if rec is None:
        st.info("No recommendation for this client (nothing fired, or everything was suppressed).")
        return
    color = CATEGORY_INFO.get(rec.nba_category, ("", "#666"))[1]
    with st.container(border=True):
        st.markdown(
            f'<span style="background:{color}; color:white; padding:2px 10px; border-radius:12px; '
            f'font-size:0.8rem; font-weight:600;">{rec.nba_category.replace("_", " ").title()}</span>'
            f'&nbsp;&nbsp;strength {rec.signal_strength}/5 - confirmed by '
            f'{", ".join(rec.confirming_domains)}',
            unsafe_allow_html=True,
        )
        st.markdown(f"**Hypothesis:** {rec.hypothesis}")
        st.markdown(f"**Recommended action:** {rec.recommended_action}")
        st.caption(f"Evidence `{rec.evidence_ref}` - sizing basis `{rec.sizing_basis}`. Category, "
                   f"sizing and strength come from `datainsights/correlation/hypothesis.py`, "
                   f"identical with or without the LLM (`tests/test_orchestrator.py` asserts it).")


def _render_fdm_trace_tab(key: str, src: dict) -> None:
    """The 'where does it all connect' view. Everything rendered here is
    already computed by agents/orchestrator.py's evaluate_client() -- the
    correlation was never missing from the pipeline, only from the UI."""
    from datetime import date, timedelta

    profile = src["profile"]
    st.caption("Start here — no need to run the whole book first. Returns in about a second.")
    with st.expander("What a trace shows", icon=":material/info:"):
        st.markdown(
            "One client through every stage: each domain agent's own evidence → the shared "
            "signal bus → the exogenous event check → the deterministic arbitration that "
            "picks ONE category and sizes ONE offer → the card the RM sees. Batch mode, no LLM."
        )
    try:
        party_ids = _client_ids(profile, src["picker_concept"])
    except Exception as e:  # noqa: BLE001 -- an unreadable source is reported, never a crash
        st.warning(f"Cannot list clients for `{profile}`: {type(e).__name__}: {e}")
        return
    if not party_ids:
        st.warning(f"No clients found for `{profile}` — has its data been generated?")
        return

    default_idx = party_ids.index("PRTY00036") if "PRTY00036" in party_ids else 0
    c1, c2 = st.columns([3, 1])
    with c1:
        prty_id = st.selectbox("Client", party_ids, index=default_idx, key=f"trace_client_{key}")
    with c2:
        st.markdown("&nbsp;")
        go = st.button("🔍 Trace this client", width="stretch", key=f"trace_go_{key}")

    if go:
        with st.spinner(f"Tracing {prty_id} through the pipeline..."):
            try:
                from agents.orchestrator import evaluate_client
                from datainsights.runtime import build_runtime
                from external_events.exposure_qualifier import load_events

                rt = build_runtime(profile)
                event = load_events(rt.event_source_path)[0] if rt.event_source_path else None
                as_of = (event.event_date + timedelta(days=90)) if event else date.today()
                evaluation = evaluate_client(
                    prty_id, source=rt.source, rules=rt.rules, as_of=as_of, event=event,
                    narrate=False, binding_name=rt.binding_name)
                st.session_state[f"trace_result_{key}"] = (prty_id, evaluation, event, as_of)
            except Exception as e:  # noqa: BLE001 -- a trace failure must not kill the tab
                st.session_state.pop(f"trace_result_{key}", None)
                st.error(f"Trace failed: {type(e).__name__}: {e}")
                return

    cached = st.session_state.get(f"trace_result_{key}")
    if not cached or cached[0] != prty_id:
        st.info("Pick a client and click **Trace this client**. Nothing else needs to have been "
               "run first — this reads the data directly.")
        return
    _, evaluation, event, as_of = cached
    st.caption(f"Traced `{prty_id}` as of **{as_of}**.")

    # ---- Stage 1: the agents ----
    st.markdown("#### Stage 1 · Domain agents run independently")
    st.caption(
        "Each agent owns ONE domain's tools and sees only that domain's evidence. They never "
        "talk to each other — that is deliberate. Collaboration happens at the signal bus in "
        "Stage 2, where their independent findings are combined by deterministic code, not by "
        "an LLM negotiating with another LLM."
    )
    domains = list(evaluation.agent_results)
    if domains:
        agent_cols = st.columns(len(domains))
        for col, domain in zip(agent_cols, domains):
            result = evaluation.agent_results[domain]
            evidence = result if isinstance(result, dict) else result.tool_evidence
            with col:
                st.markdown(f"**{domain}**")
                for tool_name, out in (evidence or {}).items():
                    status = out.get("status", "?") if isinstance(out, dict) else "?"
                    icon = {"detected": "🟢"}.get(status, "⚪")
                    st.caption(f"{icon} `{tool_name}`  \n&nbsp;&nbsp;&nbsp;{status}")

    with st.expander("Where does each agent get its data? (lineage under the active binding)"):
        from datainsights.semantic.binding import load_binding

        binding = load_binding("fdm")
        lineage_rows = []
        for read_domain, concepts in DOMAIN_READS.items():
            if read_domain not in evaluation.agent_results:
                continue
            for concept in concepts:
                cb = binding.concepts.get(concept)
                if cb is None:
                    physical, note = "-", "not declared in this binding"
                elif cb.unavailable:
                    physical, note = "-", f"unavailable: {cb.unavailable}"
                else:
                    tables = [cb.entity] + [j.entity for j in cb.joins]
                    physical = " + ".join(t for t in tables if t)
                    note = (f"{len(cb.joins)} join(s) - one concept, several tables"
                            if cb.joins else "single table")
                lineage_rows.append({"agent": read_domain, "canonical concept": concept,
                                     "physical table(s)": physical, "note": note})
        if lineage_rows:
            st.dataframe(pd.DataFrame(lineage_rows), width="stretch", hide_index=True)
        with st.expander("Why an agent never names a table", icon=":material/info:"):
            st.markdown(
                f"An agent asks for a canonical concept (`config/semantic_model.yaml`); the "
                f"active binding decides which physical table(s) that resolves to. That "
                f"indirection is why the same agents run unchanged against every schema here. "
                f"**One concept can already span several tables** — see the join counts above. "
                f"Not supported today: spanning several *databases* in one run "
                f"(one profile = one backend)."
            )
        touched = sorted({t for sig in evaluation.signals for t in sig.source_tables})
        if touched:
            st.success("Tables that actually produced a signal for this client: "
                       + ", ".join(f"`{t}`" for t in touched))

    # ---- Stage 2: the signal bus ----
    st.markdown("#### Stage 2 · The signal bus — where the agents' findings meet")
    if evaluation.signals:
        sig_df = pd.DataFrame([{
            "signal_type": s.signal_type, "domain": s.domain, "direction": s.direction,
            "magnitude": float(s.magnitude), "observed": str(s.observed_date),
            "evidence_ref": s.evidence_ref,
        } for s in evaluation.signals])
        st.dataframe(
            sig_df, width="stretch", hide_index=True,
            column_config={"magnitude": st.column_config.ProgressColumn(
                "magnitude (0-1)", min_value=0.0, max_value=1.0, format="%.2f")},
        )
        st.caption("Magnitude is normalised 0-1 so signals from different domains are directly "
                  "comparable — that comparability is what makes Stage 4's arbitration possible.")
    else:
        st.info("No endogenous signals fired for this client. That is a normal, common outcome — "
               "most clients are not doing anything noteworthy in any given window.")

    # ---- Stage 3: exogenous correlation ----
    st.markdown("#### Stage 3 · Exogenous event — correlated against this client's OWN activity")
    if event is None:
        st.caption("No exogenous event source configured for this profile.")
    else:
        st.markdown(
            f"Event in scope: **{event.event_type}** · {event.affected_sector}/{event.affected_country} "
            f"· {event.event_date} · EUR {event.estimated_value_eur:,.0f}"
        )
        if evaluation.exogenous_confirmed:
            st.success(
                "✅ **Qualified.** This client shares the event's sector AND country *and* their own "
                "data confirms genuine exposure. Both halves are required — this is the endogenous/"
                "exogenous correlation, and it is why Stage 4 adds a confidence point below."
            )
        else:
            st.warning(
                "➖ **Not qualified.** Sector + country alone is never enough: a naive match would "
                "contact every client in the sector. Without confirming activity in this client's "
                "own data, the event does not reach their recommendation at all."
            )

    # ---- Stage 4: arbitration ----
    st.markdown("#### Stage 4 · Arbitration — ONE category, ONE sized offer")
    rec = evaluation.recommendation
    if rec is None:
        st.info("No recommendation produced — either nothing fired, or everything that fired was "
               "suppressed (e.g. a high-risk-flagged client never receives a revenue category).")
        return

    # Imported deliberately: showing the category assemble() would have picked
    # from the strongest signal must use assemble()'s OWN mapping, not a
    # re-derivation that could silently drift from it.
    from datainsights.correlation.hypothesis import _category_for

    strongest = max(evaluation.signals, key=lambda s: s.magnitude)
    natural = _category_for(strongest.signal_type)
    a1, a2 = st.columns(2)
    with a1:
        st.markdown(f"**Strongest signal wins the category**  \n`{strongest.signal_type}` "
                   f"({strongest.domain}, magnitude {strongest.magnitude:.2f})")
        if getattr(rec, "combination_rule", None):
            st.info(f"**Cross-domain rule fired: `{rec.combination_rule}`** — the signals present together "
                    f"match a rule in `config/domains_fdm.yaml` `combinations:`, which sets the category "
                    f"and hypothesis instead of the strongest signal alone.")
            natural = _category_for(strongest.signal_type)
            from datainsights.domain_registry import matching_combination
            _rule = matching_combination({s.signal_type for s in evaluation.signals})
            natural = _rule.category if _rule else natural
        st.markdown(f"Category from the evidence: **{natural.replace('_', ' ').title()}**")
        if natural != rec.nba_category:
            st.error(
                f"⚠️ **Suppressed → {rec.nba_category.replace('_', ' ').title()}.** A revenue "
                f"category was downgraded because this client is high-risk-flagged, or a "
                f"RISK_REVIEW signal is present. Governance rule, applied before any RM sees it."
            )
        else:
            st.markdown(f"Final category: **{rec.nba_category.replace('_', ' ').title()}**")
    with a2:
        base = max(1, round(max(s.magnitude for s in evaluation.signals) * 3))
        domain_bonus = max(0, len({s.domain for s in evaluation.signals}) - 1)
        exo_bonus = 1 if evaluation.exogenous_confirmed else 0
        st.markdown("**Confidence, decomposed** (never one opaque number)")
        st.caption(f"strongest magnitude → base **{base}**")
        st.caption(f"+ **{domain_bonus}** for each additional confirming domain "
                  f"({', '.join(sorted({s.domain for s in evaluation.signals}))})")
        st.caption(f"+ **{exo_bonus}** for exogenous confirmation")
        capped = " (capped at 5)" if base + domain_bonus + exo_bonus > 5 else ""
        st.markdown(f"= **{rec.signal_strength}/5**{capped}")

    st.caption(f"Confirmed by: **{', '.join(rec.confirming_domains)}** · sizing basis "
              f"`{rec.sizing_basis}` · evidence `{rec.evidence_ref}`")
    if rec.exogenous_event_type and _category_registry.is_revenue(rec.nba_category):
        st.caption("Note: a confirmed exogenous event can also override the hypothesis text and "
                  "drive the offer size from the event's own value — see `config/event_types.yaml`.")

    # ---- Stage 4b: investigation (R9, Tier 2) ----
    if getattr(rec, "investigation", None):
        inv = rec.investigation
        st.markdown("#### Stage 4b · Investigation — a second look, as a proposal only")
        st.caption(
            "Triggered because this recommendation is ambiguous or confirmed by more than one domain. "
            "The investigator (`agents/investigator_agent.py`) read ONLY this client's own record through "
            "read-only tools, and every proposal is re-checked in Python against the same facts before "
            "it is shown. It never changes the category above — an RM does."
        )
        status = inv.get("status")
        if status == "proposed":
            st.warning(f"Proposes **{inv['proposed_category'].replace('_', ' ').title()}** instead of "
                       f"**{rec.nba_category.replace('_', ' ').title()}** — {inv.get('reasoning', '')}")
        elif status == "no_change_proposed":
            st.success(f"Investigated and agrees with **{rec.nba_category.replace('_', ' ').title()}** — "
                       f"{inv.get('reasoning', '')}")
        else:
            st.info(f"Needs review — {inv.get('could_not_determine', '')}")
        if inv.get("evidence_refs"):
            st.caption("Evidence consulted: " + ", ".join(f"`{e}`" for e in inv["evidence_refs"]))
        st.caption(f"Source: `{inv.get('narrative_source', '')}`")
    # ---- Stage 5: the RM card ----
    st.markdown("#### Stage 5 · What the RM actually sees")
    color = CATEGORY_INFO.get(rec.nba_category, ("", "#666"))[1]
    with st.container(border=True):
        st.markdown(
            f'<span style="background:{color}; color:white; padding:2px 10px; border-radius:12px; '
            f'font-size:0.8rem; font-weight:600;">{rec.nba_category.replace("_", " ").title()}</span>'
            f'&nbsp;&nbsp;strength {rec.signal_strength}/5',
            unsafe_allow_html=True,
        )
        st.markdown(f"**Hypothesis:** {rec.hypothesis}")
        st.markdown(f"**Recommended action:** {rec.recommended_action}")
        if rec.sized_offer_eur:
            st.markdown(f"**Indicative offer:** EUR {rec.sized_offer_eur:,.0f} _(illustrative)_")
        st.caption("Category, sizing and strength above were all decided by deterministic code. "
                  "An LLM only writes narration around them, and is validated against this "
                  "evidence before its text is accepted.")


def _client_ids(profile: str, concept: str) -> list[str]:
    """Client ids for the picker, read through CanonicalSource.

    Previously this read data_generator/output_fdm/kernel/party.csv and its
    PRTY_ID column directly -- which worked for exactly one schema. Going
    through the binding means the picker works for any profile, and the
    dashboard stops depending on one schema's physical table and column
    names (the coupling tests/test_no_source_specific_coupling.py forbids
    elsewhere)."""
    from datainsights.runtime import build_runtime
    from datainsights.semantic.binding import load_binding
    from datainsights.semantic.canonical import CanonicalSource

    runtime = build_runtime(profile)
    canonical = CanonicalSource(runtime.source, load_binding(runtime.binding_name))
    frame = canonical.read(concept)
    return sorted(frame["party_id"].dropna().unique().tolist())


def _render_fdm_source(key: str, src: dict) -> None:
    profile = src["profile"]
    st.caption(f"Profile `{profile}` · the three tabs are independent — run any one alone.")
    trace_tab, vol_tab, depth_tab = st.tabs(
        [":material/search: Trace one client",
         ":material/list_alt: Whole-book worklist",
         ":material/auto_awesome: Live narration"])
    with trace_tab:
        _render_fdm_trace_tab(key, src)
    with vol_tab:
        _render_fdm_worklist_tab(key, src)
    with depth_tab:
        _render_fdm_live_demo_tab(key, src)


def _render_proof_only_source(key: str, src: dict) -> None:
    # Reached when a profile cannot drive the per-client tabs -- its binding
    # does not supply the picker concept, or it has no local data. The old
    # text here claimed no whole-book generator existed for such a source,
    # which was stale after R23 and wrong for legacy and SBA (167 and 131
    # recommendations respectively). `kind` is computed from capability now,
    # so this branch describes a real limitation rather than a stale label.
    st.warning(
        f"This source cannot drive the per-client tabs — its binding "
        f"(`{src.get('binding')}`) does not supply the `{PICKER_CONCEPT}` concept, or it has "
        f"no local data directory. Its sampled proof still runs.",
        icon=":material/info:")
    if st.button(f"▶ Run: {src['proof_label']}", key=f"run_proof_{key}"):
        run_module(["pytest", src["proof_test"], "-q"], src["proof_label"])
    entry = next((e for e in st.session_state.get("logs", []) if e["label"] == src["proof_label"]), None)
    if entry:
        if entry["ok"]:
            st.success(src.get("proof_success", "Passed — see raw output for detail."))
        else:
            st.error("Failed — see log.")
        with st.expander("Raw test output"):
            st.code(entry["out"] or entry["err"], language="text")


def render() -> None:
    source_key = st.selectbox(
        "Data source", list(DATA_SOURCES),
        format_func=lambda k: DATA_SOURCES[k]["label"],
        key="explore_source",
    )
    src = DATA_SOURCES[source_key]

    with st.expander("About this source", icon=":material/info:"):
        st.markdown(src["description"])
        st.caption(
            "What you can do differs per source and is disclosed here, not hidden behind "
            "which tab you happened to click. Sources are discovered from "
            "`config/profiles/` \u2014 onboarding one makes it appear here with no code change."
        )
    st.divider()

    _render_source_data_panel(source_key, src)
    _render_exogenous_panel()

    if src["kind"] == "full_agentic":
        _render_fdm_source(source_key, src)
    else:
        _render_proof_only_source(source_key, src)
