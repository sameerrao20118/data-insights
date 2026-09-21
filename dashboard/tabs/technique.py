"""Technique reference page -- split out of dashboard/app.py by R22. Rendering only; every shared
helper comes from dashboard/common.py."""

from dashboard.common import *  # noqa: F401,F403 -- the page bodies were written against these names
from dashboard.common import _category_registry  # noqa: F401

def render() -> None:
    st.caption("Where each kind of logic sits, and why the rest isn't ML yet.")
    tech_df = pd.DataFrame(TECHNIQUE_ROWS, columns=["Stage", "Technique", "Why"])
    st.dataframe(tech_df, width="stretch", hide_index=True)
    st.divider()

    # Counted, not asserted. The heading used to read "The six
    # recommendation categories" while the list below it was built from
    # config/categories.yaml -- so adding a seventh category produced a
    # page listing seven under a heading saying six.
    st.subheader(f"Recommendation categories ({len(CATEGORY_INFO)})")
    st.caption("From `config/categories.yaml` — adding one is a YAML edit, "
               "but a category with no detector behind it can never reach a worklist.")
    for cat, (desc, _colour) in CATEGORY_INFO.items():
        revenue = _category_registry.revenue_model(cat) != "none"
        st.markdown(
            f"{':material/trending_up:' if revenue else ':material/shield:'} "
            f"**{cat.replace('_', ' ').title()}** — {desc}")

    st.divider()
    _render_signals()


def _render_signals() -> None:
    """Every signal the system knows about, live AND shadow.

    Three discovered signals existed in config/domains_discovered.yaml with
    no way to see them from the dashboard, which made the discovery
    pipeline invisible: a reviewer could not tell what had been proposed,
    on what evidence, or that it was being kept out of the worklist."""
    from datainsights.domain_registry import _all_signals
    from onboarding.signal_accept import load_discovered

    active = _all_signals()
    st.subheader(f"Signals ({len(active)} live)")
    st.caption("A signal is what a detector emits; the registry maps it to a category. "
               "Live signals can reach an RM worklist.")
    st.dataframe(
        pd.DataFrame([{"signal": name, "category": spec.get("category", ""),
                       "why now": spec.get("why_now", "")}
                      for name, spec in sorted(active.items())]),
        width="stretch", hide_index=True)

    discovered = load_discovered()
    shadow = [(d, n, e) for d, blk in discovered.items()
              for n, e in (blk.get("signals") or {}).items()]
    if not shadow:
        st.caption(":material/science: No discovered signals yet. "
                   "`python -m onboarding.discover_signals --profile <name>` proposes some.")
        return

    st.subheader(f"Discovered — in shadow ({len(shadow)})")
    st.warning(
        "Machine-proposed and human-accepted, but **held out of the pipeline**. A shadow "
        "signal cannot resolve a category in `assemble()`, so it can never reach a worklist. "
        "Promotion is a deliberate human edit, or a backtest hit rate once RM outcomes exist.",
        icon=":material/visibility_off:")
    st.dataframe(
        pd.DataFrame([{
            "signal": n, "domain": d, "proposed category": e.get("category", ""),
            "status": e.get("status", ""),
            "confidence": (e.get("provenance") or {}).get("proposer_confidence"),
            "clients flagged": ((e.get("provenance") or {}).get("screening") or {}).get("clients_flagged"),
            "overlap with existing": ((e.get("provenance") or {}).get("screening") or {}).get("max_overlap_with_existing"),
            "accepted by": (e.get("provenance") or {}).get("accepted_by", ""),
        } for d, n, e in shadow]),
        width="stretch", hide_index=True,
        column_config={
            "confidence": st.column_config.ProgressColumn(
                "Proposer confidence", min_value=0.0, max_value=1.0, format="%.2f"),
            "overlap with existing": st.column_config.NumberColumn(
                "Overlap with existing", format="%.0f%%"),
        })
    st.caption("Overlap is how much a discovered signal flags the same clients an existing "
               "signal already does — low overlap is what made it worth proposing. Novelty "
               "is measurable today; **value is not**, until RM outcomes exist.")
