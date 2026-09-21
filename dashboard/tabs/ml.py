"""ML opportunities page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    st.caption(
        "Self-service ML, in three steps: 1) scan a schema to see which fields have "
        "genuinely enough history for a challenger baseline to mean anything -- deterministic, "
        "no guessing; 2) decide -- accept what's automatically enabled, turn a measure on/off "
        "yourself, and Save; 3) run deterministic-vs-challenger and read a real result, never "
        "a verdict on its own. Full design: `docs/ml_strategy_plan.md`."
    )

    schema_key = st.selectbox("Schema", list(DATA_SOURCES),
                              format_func=lambda k: DATA_SOURCES[k]["label"], key="ml_schema")
    data_dir = DATA_SOURCES[schema_key].get("data_dir")

    st.markdown("### Step 1 — Scan for eligible measures")
    from datainsights.ml.policy import load_power_criteria, outcome_label_count
    _pc = load_power_criteria()
    _labels = outcome_label_count()
    st.caption(
        "Deterministic, no LLM call (`onboarding/ml_profiler.py`). Two verdicts per numeric column, "
        "because they are two different claims (`config/ml_policy.yaml` → `power_criteria`): "
        f"**robust-baseline eligible** — enough per-entity history (≥ {_pc.robust_baseline.min_observations_per_entity} "
        f"obs/entity, ≥ {_pc.robust_baseline.min_entities} entities, a time column, not >20% null, not constant) "
        "to compare the deterministic baseline against SLOT E2's outlier-robust statistic; and "
        f"**ML-challenger eligible** — a population (≥ {_pc.ml_challenger.min_entities} entities), history longer "
        f"than the window (≥ {_pc.ml_challenger.window_points * _pc.ml_challenger.min_history_multiple_of_window} "
        f"obs/entity) and an evaluation protocol (≥ {_pc.ml_challenger.min_outcome_labels} RM outcome labels; "
        f"**{_labels} recorded today**). Every rejection names the criterion it failed."
    )
    if not data_dir or not Path(data_dir).exists():
        st.warning(f"No data found at `{data_dir}` - generate it first, or add the source "
                  f"via the **Onboard a source** tab.")
    else:
        if st.button("🔍 Scan this schema for ML opportunities", key="ml_scan", width="stretch"):
            from onboarding.ml_profiler import assess
            from onboarding.profiler import profile_directory

            profiles = profile_directory(str(data_dir))
            st.session_state["ml_eligibility"] = assess(str(data_dir), profiles)
            st.session_state["ml_eligibility_schema"] = schema_key

        eligibility = (st.session_state.get("ml_eligibility")
                      if st.session_state.get("ml_eligibility_schema") == schema_key else None)
        if eligibility is None:
            st.info("Click **Scan this schema** above — takes under a second, reads a bounded "
                   "sample of each table, nothing is written anywhere.")
        else:
            rows = []
            for table, cols in eligibility.items():
                for c in cols:
                    rows.append({
                        "Table": table, "Column": c.column,
                        "Robust baseline": "✅ Yes" if c.eligible else "❌ No",
                        "ML challenger": "✅ Yes" if c.ml_challenger_eligible else "❌ No",
                        "Entity column": c.entity_column or "—",
                        "Obs/entity": f"{c.observations_per_entity:.1f}" if c.observations_per_entity is not None else "—",
                        "Why not": "; ".join(c.reasons or c.ml_reasons),
                    })
            if rows:
                df_elig = pd.DataFrame(rows)
                st.dataframe(df_elig, width="stretch", hide_index=True,
                            column_config={"Why not": st.column_config.TextColumn(width="large")})
                n_eligible = (df_elig["Robust baseline"] == "✅ Yes").sum()
                st.caption(f"**{n_eligible} of {len(df_elig)}** numeric columns across "
                          f"{len(eligibility)} tables can support a robust-baseline comparison.")
                from onboarding.ml_profiler import ml_challenger_verdict
                _ok, _why = ml_challenger_verdict(eligibility)
                if _ok:
                    st.success("At least one measure meets the ML-challenger power criteria on this dataset.")
                else:
                    st.error("**No ML challenger is eligible on this dataset yet — deterministic and "
                             "robust baselines are the right choice here.** Closest measure failed: "
                             + " · ".join(_why))
            else:
                st.info("No numeric columns found in this schema's tables to assess.")

    st.divider()
    # The single most important fact on this page, placed where the
    # misleading control actually lives. Verified by grep: NOTHING in
    # agents/, detection_engine/, datainsights/fdm_worklist.py or
    # datainsights/correlation/ reads config/ml_policy.yaml -- only this
    # tab and datainsights/ml/runner.py do. So these toggles cannot and do
    # not change what an RM sees. Read the production baseline live from
    # config/rules.yaml so this statement can never drift from the truth.
    import yaml as _yaml

    with open(ROOT / "config" / "rules.yaml") as _f:
        _rules_now = _yaml.safe_load(_f) or {}
    _prod_baselines = {k: (_rules_now.get(k) or {}).get("baseline", "deterministic")
                       for k in ("cash_buildup", "revenue_pattern_change")}
    st.warning(
        "**This tab does not change what an RM sees.** These toggles drive the "
        "champion/challenger **comparison in Step 3 only**. The live pipeline - both the "
        "whole-book worklist and live narration - takes its baseline from "
        "`config/rules.yaml`, which right now says: "
        + ", ".join(f"`{k}: {v}`" for k, v in _prod_baselines.items())
        + ". Promoting a challenger into production means changing that file deliberately - "
        "a separate, human step, by design."
    )

    st.markdown("### Step 2 — Decide: accept, override, or wait")
    st.caption(
        "Precedence, highest first: your explicit choice below → a measure the active binding "
        "already maps to a canonical concept (enabled automatically, no config needed) → an "
        "LLM-proposed extra measure held back until a human accepts it. Nothing here trains a "
        "model — this only decides what a comparison run (Step 3) is allowed to challenge."
    )
    st.caption(
        "⚠️ **This list is narrower than Step 1's scan, on purpose.** Step 1 tells you which "
        "columns *could* support a challenger. The toggles below cover only the measures that "
        "also have a comparison wired in `datainsights/ml/runner.py`'s `MEASURE_COMPARISONS` — "
        "being eligible is necessary but not sufficient to be runnable. Anything eligible but "
        "not yet wired is listed underneath so the gap is visible rather than silently dropped."
    )
    from datainsights.ml.policy import VALID_ALGORITHMS, load_policy
    from datainsights.ml.runner import MEASURE_COMPARISONS

    policy = load_policy(schema_key)
    edits: dict[str, tuple[bool, str]] = {}
    for measure in MEASURE_COMPARISONS:
        resolved = policy.resolve(measure, gate1_eligible=True, mapped_by_binding=True)
        c1, c2, c3 = st.columns([3, 1, 2])
        with c1:
            st.markdown(f"**{measure}**")
            note = f"chosen by: `{resolved.chosen_by}`"
            if resolved.reason:
                note += f" — {resolved.reason}"
            st.caption(note)
        with c2:
            enabled = st.checkbox("Enabled", value=resolved.enabled,
                                  key=f"ml_enable_{schema_key}_{measure}")
        with c3:
            default_idx = VALID_ALGORITHMS.index(resolved.algorithm) if resolved.algorithm in VALID_ALGORITHMS else 0
            algo = st.selectbox("Algorithm", VALID_ALGORITHMS, index=default_idx,
                                key=f"ml_algo_{schema_key}_{measure}", label_visibility="collapsed")
        edits[measure] = (enabled, algo)

    scan = (st.session_state.get("ml_eligibility")
            if st.session_state.get("ml_eligibility_schema") == schema_key else None)
    if scan:
        wired_columns = {"balance", "amount"}  # the physical measures MEASURE_COMPARISONS covers
        unwired = [f"`{t}.{c.column}`" for t, cols in scan.items() for c in cols
                   if c.eligible and c.column.lower() not in wired_columns]
        if unwired:
            st.info(
                f"**Eligible in Step 1 but not runnable yet:** {', '.join(unwired)}. "
                f"To make one of these comparable, add it to "
                f"`datainsights/ml/runner.py`'s `MEASURE_COMPARISONS` with the detector that "
                f"should challenge it — that is the only registration point, by design."
            )

    if st.button("💾 Save policy", key="ml_save_policy", width="stretch"):
        save_ml_policy(schema_key, edits)
        st.success(f"Saved to `config/ml_policy.yaml` for schema {schema_key!r}. "
                  f"This affects the Step 3 comparison ONLY - the whole-book worklist "
                  f"and live narration are unchanged, and still use "
                  f"config/rules.yaml's baseline.")
        st.cache_data.clear()

    st.divider()
    st.markdown("### Step 3 — Run deterministic vs. robust baseline (SLOT E2)")
    st.caption("E2 is an outlier-robust per-entity statistic — an IsolationForest drops a client's own past "
               "spikes before median/MAD. It never learns across clients and never sees a label, so it is a "
               "**robust baseline**, not an ML model in the model-risk sense (`docs/hardcoding_audit.md` §3).")
    st.warning(
        "⚠️ A disagreement is NOT an improvement claim. Without real outcome labels (RM "
        "engagement results), there is no way to say which baseline is right — this tells "
        "you WHAT changed, never what's better. See `docs/gap_analysis.md`'s SLOT E2 row."
    )
    if schema_key != "fdm":
        st.info(
            f"Champion/challenger comparisons are wired for the `fdm` schema only today "
            f"(`datainsights/ml/runner.py`'s `MEASURE_COMPARISONS`) — a real, disclosed scope "
            f"limit, not a hidden one. Extending it to {schema_key!r} is "
            f"`docs/ml_strategy_plan.md` T4's own next step."
        )
    else:
        # The profile follows the schema picked above, not a fixed one --
        # running fdm_local's comparison while the user is looking at
        # another schema reports a result for data they did not select.
        _ml_profile = DATA_SOURCES[schema_key].get("profile", schema_key)
        if st.button(f"▶ Run champion vs challenger ({_ml_profile})",
                     key="ml_run_runner", width="stretch"):
            run_module(["datainsights.ml.runner", "--profile", _ml_profile],
                       f"ML champion/challenger run ({_ml_profile})")
        entry = next((e for e in st.session_state.get("logs", [])
                     if e["label"] == "ML champion/challenger run"), None)
        if entry:
            with st.expander(f"{'✅' if entry['ok'] else '❌'} Run output", expanded=True):
                st.code(entry["out"] or entry["err"], language="text")

    st.divider()
    st.markdown("### Model registry")
    st.caption(
        "`var/models/<name>/<version>/{model.joblib, card.json}` — empty until a real model "
        "is trained and registered. SLOT E4 (propensity) stays unimplemented by design, "
        "pending real RM-feedback label volume (`docs/gap_analysis.md`)."
    )
    from dataclasses import asdict as _asdict

    from datainsights.ml.model_registry import list_models, load_card

    models = list_models()
    if not models:
        st.info("No models registered yet.")
    else:
        for name, version in models:
            try:
                card = load_card(name, version)
                with st.expander(f"{name} / {version}"):
                    st.json(_asdict(card))
            except Exception as e:  # noqa: BLE001 -- a bad card must not break the whole listing
                st.caption(f"{name}/{version}: could not load card ({type(e).__name__}: {e})")
