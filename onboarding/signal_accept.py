"""
Stages D and E of signal discovery (docs/signal_discovery_design.md
recs. 7-8): the human acceptance gate, and shadow status.

Stage D -- ACCEPTANCE. An accepted proposal is written to
config/domains_discovered.yaml and a spec file under
detection_engine/specs/discovered/. Two deliberate properties:

  * config/domains_fdm.yaml is NEVER written by this tooling. It stays a
    hand-authored file. A discovered signal lives in its own file, so a
    reviewer can diff exactly what a machine proposed against what people
    wrote, and `git revert` one without touching the other.

  * Acceptance is a separate, explicit, human step -- same rule that
    already governs onboarding/accept.py (bindings) and the dashboard's
    "Save policy" action (ML policy). Nothing in propose_signals() writes
    config.

Stage E -- SHADOW. Every accepted signal is written with `status: shadow`.
A shadow signal:
  * runs, and is visible in Explore/Trace
  * is NOT read by datainsights/domain_registry.py, so
    datainsights/correlation/hypothesis.py::assemble() cannot see its
    category and cannot put it in a Recommendation
  * therefore never reaches an RM worklist

Promotion to `active` is a deliberate human edit, or -- once RM outcome
labels exist -- a datainsights/backtest.py hit rate. That is the honest
resolution of the ground-truth bottleneck: discovery can establish that a
pattern is NOVEL today; only outcomes can establish that it is VALUABLE.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

import yaml

from onboarding.signal_proposer import SignalProposal

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DISCOVERED_DOMAINS_PATH = os.path.join(_ROOT, "config", "domains_discovered.yaml")
DISCOVERED_SPECS_DIR = os.path.join(_ROOT, "detection_engine", "specs", "discovered")

_HEADER = """\
# DISCOVERED signals -- written by onboarding/signal_accept.py, reviewed by a human.
#
# This file is machine-written. config/domains_fdm.yaml is NOT: it stays
# hand-authored, and no tooling in this repo writes to it. Keeping the two
# apart means a reviewer can diff what a model proposed against what people
# wrote, and revert one without disturbing the other.
#
# Every entry here is `status: shadow`. A shadow signal runs and is visible
# in the dashboard's Explore/Trace views, but datainsights/domain_registry.py
# does not read this file, so datainsights/correlation/hypothesis.py cannot
# resolve its category and it can never enter a Recommendation or an RM
# worklist.
#
# Promoting a signal to `active` is a deliberate human act: move the entry
# into config/domains_fdm.yaml under the owning domain. Do that only when
# either a reviewer has justified it, or datainsights/backtest.py shows a
# hit rate against real RM outcomes.
"""


def _render_entry(proposal: SignalProposal) -> dict:
    screening = proposal.screening or {}
    return {
        "category": proposal.category,
        "status": "shadow",
        "why_now": proposal.why_now,
        "hypothesis": proposal.hypothesis,
        "origin": "discovered",
        "provenance": {
            "candidate_signal_type": proposal.candidate_signal_type,
            "archetype": proposal.archetype,
            "proposer_confidence": round(float(proposal.confidence), 3),
            "proposer_caveats": proposal.caveats,
            "screening": {
                "fire_rate": round(float(screening.get("fire_rate", 0.0)), 4),
                "clients_flagged": int(screening.get("n_flagged", 0)),
                "max_overlap_with_existing": round(float(screening.get("max_overlap", 0.0)), 4),
                "overlaps_with": screening.get("overlaps_with", ""),
            },
            "accepted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    }


def accept(proposals: list[SignalProposal], *, accepted_by: str,
           domains_path: str | None = None, specs_dir: str | None = None) -> dict:
    """Write accepted proposals to the discovered-signals config and spec
    directory. Returns a summary for the caller to display.

    `accepted_by` is required and recorded: a discovered signal must carry
    the name of the person who let it in."""
    if not accepted_by or not str(accepted_by).strip():
        raise ValueError("accept() requires accepted_by -- a discovered signal records who let it in")

    domains_path = domains_path or DISCOVERED_DOMAINS_PATH
    specs_dir = specs_dir or DISCOVERED_SPECS_DIR
    os.makedirs(specs_dir, exist_ok=True)

    existing: dict = {}
    if os.path.exists(domains_path):
        with open(domains_path) as f:
            existing = yaml.safe_load(f) or {}

    written, skipped = [], []
    for proposal in proposals:
        if not proposal.accepted or proposal.spec is None:
            skipped.append({"signal": proposal.candidate_signal_type,
                            "reason": proposal.rejected_reason or "not accepted"})
            continue

        name = proposal.spec.signal_type
        domain = proposal.spec.domain
        domain_block = existing.setdefault(domain, {})
        signals_block = domain_block.setdefault("signals", {})

        # Distinct candidates can be proposed under the SAME business name
        # -- measured: three transaction-deviation candidates differing only
        # in window/k all came back as "transaction_amount_deviation". They
        # are genuinely different rules, so disambiguate rather than drop
        # (silently keeping one of three would misrepresent what was
        # reviewed). The suffix names the parameters that differ.
        if name in signals_block:
            existing_candidate = (signals_block[name].get("provenance") or {}).get(
                "candidate_signal_type")
            if existing_candidate == proposal.candidate_signal_type:
                skipped.append({"signal": name, "reason": "already accepted from this candidate"})
                continue
            suffix = proposal.candidate_signal_type.rsplit("_", 2)[-2:]
            name = f"{name}_{'_'.join(suffix)}"
            if name in signals_block:
                skipped.append({"signal": name, "reason": "already present in discovered config"})
                continue

        entry = _render_entry(proposal)
        entry["provenance"]["accepted_by"] = accepted_by
        signals_block[name] = entry

        # `name` may have been disambiguated above -- the spec file and the
        # domains entry must agree, or the executor would look for a spec
        # that is not there.
        spec_payload = {
            "signal_type": name, "archetype": proposal.spec.archetype,
            "domain": domain, "concept": proposal.spec.concept,
            "grain": list(proposal.spec.grain), "direction": proposal.spec.direction,
            "params": proposal.spec.params, "origin": "discovered",
            "status": "shadow", "notes": proposal.spec.notes,
        }
        with open(os.path.join(specs_dir, f"{name}.yaml"), "w") as f:
            yaml.safe_dump(spec_payload, f, sort_keys=False, default_flow_style=False)
        written.append(name)

    with open(domains_path, "w") as f:
        f.write(_HEADER)
        f.write(f"# Last written: {date.today().isoformat()} by {accepted_by}\n\n")
        yaml.safe_dump(existing, f, sort_keys=False, default_flow_style=False, width=88)

    return {"written": written, "skipped": skipped,
            "domains_path": domains_path, "specs_dir": specs_dir}


def load_discovered(domains_path: str | None = None) -> dict:
    """Read the discovered-signals config. Deliberately NOT wired into
    datainsights/domain_registry.py -- see this module's docstring. The
    dashboard reads this directly to show shadow signals."""
    domains_path = domains_path or DISCOVERED_DOMAINS_PATH
    if not os.path.exists(domains_path):
        return {}
    with open(domains_path) as f:
        return yaml.safe_load(f) or {}


def shadow_signal_types(domains_path: str | None = None) -> frozenset[str]:
    out = set()
    for domain, block in load_discovered(domains_path).items():
        for name, entry in (block.get("signals") or {}).items():
            if entry.get("status") == "shadow":
                out.add(name)
    return frozenset(out)
