"""Discover signals page.

The deterministic detectors are one half of what this system does; the
other is finding signals in a client book that nobody wrote a rule for.
That half previously had no UI at all -- it ran from the CLI and wrote a
JSON file, so the only way to review a proposal was to read
onboarding/proposals/signals/review_<profile>.json by hand.

Same shape as "Onboard a source": propose, review, accept. Nothing here
writes to config/ until Accept is clicked, and an accepted signal lands in
SHADOW -- visible, but structurally unable to reach an RM worklist until a
human promotes it. See docs/signal_discovery_design.md.
"""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

PROPOSALS_DIR = ROOT / "onboarding" / "proposals" / "signals"


def _review_path(profile: str) -> Path:
    return PROPOSALS_DIR / f"review_{profile}.json"


def _load_review(profile: str) -> dict | None:
    import json

    path = _review_path(profile)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:  # noqa: BLE001 -- a corrupt review is reported, not fatal
        st.error(f"Could not read {path.name}: {type(e).__name__}: {e}")
        return None


def _render_shadow_register() -> None:
    """What has already been accepted, and the gate it sits behind."""
    from onboarding.signal_accept import load_discovered

    shadow = [(d, n, e) for d, blk in load_discovered().items()
              for n, e in (blk.get("signals") or {}).items()]
    if not shadow:
        return

    st.subheader(f"In shadow ({len(shadow)})")
    st.warning(
        "Accepted, and deliberately **held out of the pipeline**. "
        "`datainsights/domain_registry.py` does not read the discovered file, so "
        "`assemble()` cannot resolve these categories and they cannot reach an RM worklist. "
        "Promotion is a separate human act — or a backtest hit rate, once RM outcomes exist.",
        icon=":material/visibility_off:")
    st.dataframe(
        pd.DataFrame([{
            "signal": n, "domain": d, "proposed category": e.get("category", ""),
            "confidence": (e.get("provenance") or {}).get("proposer_confidence"),
            "clients flagged": ((e.get("provenance") or {}).get("screening") or {}).get("clients_flagged"),
            "overlap": ((e.get("provenance") or {}).get("screening") or {}).get("max_overlap_with_existing"),
            "accepted by": (e.get("provenance") or {}).get("accepted_by", ""),
        } for d, n, e in shadow]),
        width="stretch", hide_index=True,
        column_config={
            "confidence": st.column_config.ProgressColumn(
                "Proposer confidence", min_value=0.0, max_value=1.0, format="%.2f"),
            "overlap": st.column_config.NumberColumn("Overlap with existing", format="%.0f%%"),
        })


def render() -> None:
    st.caption(
        "Find signals in a client book that nobody wrote a rule for — then decide, as a "
        "human, whether any of them deserve to influence an RM's worklist."
    )
    with st.expander("What discovery can and cannot establish", icon=":material/info:"):
        st.markdown(
            "Screening measures **novelty**: whether a candidate flags clients the existing "
            "detectors do not already flag. That is computable today.\n\n"
            "It cannot measure **value** — whether acting on it earns anything — because "
            "that needs RM outcome labels, and there are none yet. This is why every "
            "accepted signal lands in shadow rather than going live. A wrong baseline "
            "degrades a known signal; a signal invented from noise fabricates an entire "
            "recommendation that should never have existed."
        )

    profiles = sorted({s["profile"] for s in DATA_SOURCES.values()})
    profile = st.selectbox("Source", profiles, key="discover_profile")

    st.divider()
    st.markdown("**1 · Enumerate and screen** — deterministic, no LLM")
    st.caption(
        "Instantiates every archetype over the canonical fields this source's binding "
        "supplies, prunes what is structurally hopeless, then runs each candidate across "
        "several as-of dates: does it fire on a meaningful minority, stay stable over time, "
        "and flag clients existing signals miss?"
    )
    c1, c2 = st.columns([1, 1])
    with c1:
        use_llm = st.toggle("Also name them (local Ollama)", value=False, key="discover_llm",
                            help="Step 2. Without this, candidates are screened but unnamed.")
    with c2:
        max_props = st.number_input("Max proposals", 1, 25, 5, key="discover_max",
                                    help="Caps LLM calls — this is a review aid, not a sweep.")

    label = f"Discover signals — {profile}"
    if st.button(":material/science: Run discovery", width="stretch", key="discover_run"):
        args = ["onboarding.discover_signals", "--profile", profile]
        args += ["--max-proposals", str(int(max_props))] if use_llm else ["--no-llm"]
        run_module(args, label)

    entry = next((e for e in st.session_state.get("logs", []) if e["label"] == label), None)
    if entry and not entry["ok"]:
        st.error("Discovery failed — see log.")
        with st.expander("Raw output"):
            st.code(entry["out"] or entry["err"], language="text")

    review = _load_review(profile)
    if review is None:
        st.info(f"No discovery run for `{profile}` yet — click **Run discovery** above.")
        st.divider()
        _render_shadow_register()
        return

    st.divider()
    st.markdown("**2 · Review what survived**")
    screened = review.get("screened") or []
    passed = [r for r in screened if r.get("passed")]
    m1, m2, m3 = st.columns(3)
    m1.metric("Candidates enumerated", review.get("candidates_enumerated", len(screened)))
    m2.metric("Passed screening", len(passed))
    m3.metric("Rejected", len(screened) - len(passed))
    st.caption(f"As-of dates: {', '.join(review.get('as_of_dates') or [])}")

    if passed:
        st.markdown("**Passed** — novel relative to what this book already detects")
        st.dataframe(
            pd.DataFrame([{"candidate": r["signal_type"], "fires on": r["fire_rate"],
                           "clients": r["n_flagged"], "overlap": r["max_overlap"]}
                          for r in passed]),
            width="stretch", hide_index=True,
            column_config={
                "fires on": st.column_config.NumberColumn("Fires on", format="%.1f%%"),
                "overlap": st.column_config.NumberColumn("Overlap with existing", format="%.0f%%"),
            })

    rejected = [r for r in screened if not r.get("passed")]
    if rejected:
        with st.expander(f"Rejected ({len(rejected)}) — and why", icon=":material/filter_alt:"):
            st.caption("Every rejection carries its reason. A candidate firing on almost every "
                       "client is a threshold, not a signal.")
            st.dataframe(
                pd.DataFrame([{"candidate": r["signal_type"],
                               "reason": r.get("rejected_reason", "")} for r in rejected]),
                width="stretch", hide_index=True)

    combos = review.get("combination_candidates") or []
    if combos:
        with st.expander(f"Candidate combination rules ({len(combos)})", icon=":material/hub:"):
            st.caption(
                "Pairs co-occurring on the same client more than chance predicts — the same "
                "intuition behind the hand-written `combinations:` rules. Reported only; "
                "nothing proposes the rule text yet."
            )
            st.dataframe(pd.DataFrame(combos), width="stretch", hide_index=True)

    st.divider()
    st.markdown("**3 · Accept into shadow** — the only step that writes config")
    proposals = review.get("proposals") or []
    accepted = [p for p in proposals if p.get("accepted")]
    if not proposals:
        st.info("No named proposals in this run. Re-run with **Also name them** on to get "
                "a business name, category and hypothesis for each candidate that passed.")
        st.divider()
        _render_shadow_register()
        return

    for payload in proposals:
        if not payload.get("accepted"):
            st.caption(f":material/block: `{payload.get('candidate_signal_type')}` — "
                       f"{payload.get('rejected_reason')}")
            continue
        with st.container(border=True):
            st.markdown(f"**{payload['proposed_name']}** · `{payload.get('category')}`")
            st.caption(payload.get("hypothesis", ""))
            screening = payload.get("screening") or {}
            st.caption(
                f"Proposer confidence {payload.get('confidence', 0):.2f} · "
                f"{screening.get('n_flagged', 0)} clients · "
                f"{float(screening.get('max_overlap') or 0):.0%} overlap with existing signals"
            )

    if not accepted:
        st.divider()
        _render_shadow_register()
        return

    st.caption(
        "Accepting writes to `config/domains_discovered.yaml` with `status: shadow`. The "
        "hand-authored registry is never touched by this tooling."
    )
    who = st.text_input("Accepted by", key="discover_accepted_by",
                        placeholder="your.name@bank",
                        help="Recorded with the signal — a discovered rule carries who let it in.")
    if st.button(f":material/check: Accept {len(accepted)} signal(s) into shadow",
                 width="stretch", key="discover_accept", disabled=not who.strip()):
        from onboarding.signal_accept import accept, proposals_from_review

        rebuilt = proposals_from_review(review)
        if not rebuilt:
            st.error("Could not rebuild the proposals from this review file — re-run discovery. "
                     "(Reviews written before the spec was serialised cannot be accepted.)")
        else:
            try:
                result = accept(rebuilt, accepted_by=who.strip())
            except Exception as e:  # noqa: BLE001 -- surfaced, never silently swallowed
                st.error(f"Accept failed, nothing written: {type(e).__name__}: {e}")
            else:
                if result["written"]:
                    st.success(f"Accepted into shadow: {', '.join(result['written'])}")
                for skip in result["skipped"]:
                    st.caption(f":material/info: skipped `{skip['signal']}` — {skip['reason']}")
    if not who.strip():
        st.caption("Enter a name to enable Accept.")

    st.divider()
    _render_shadow_register()
