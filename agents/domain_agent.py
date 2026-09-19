"""
DomainAgent: a Strands agent bounded to one domain's tools, with the same
validate-or-fallback discipline as datainsights/narrative/ollama_narrator.py
-- schema check, numeric-consistency check, allowed-action check, and a
deterministic (no-LLM) fallback on any failure. This is the mechanism that
makes "tool-calling agent, not autonomous" real: the agent orchestrates
which deterministic tools to call and narrates the result, but every
number in the output must be traceable to a tool return value, and the
system degrades to a template -- never to an unvalidated LLM claim -- on
any failure, exactly like the existing narrator.

Deliberately does NOT decide the final recommendation category or size an
offer -- that stays with datainsights/worklist.py's existing lookup-table
approach and, for cross-domain evidence, the new correlation layer
(datainsights/correlation/, M6). A DomainAgent's output is one domain's
evidence + hypothesis + narrative, not a final NBA.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel

from datainsights.domain_registry import UnknownDomainError
from datainsights.domain_registry import allowed_actions as _allowed_actions
from datainsights.prompts import load_prompt


class DomainNarrative(BaseModel):
    observed_facts: str
    hypothesis: str
    suggested_action: str
    caveats: str

# Domain agents narrate detector evidence only -- they must never surface
# FinCrime/PEP/geopolitical language, even accidentally, since that
# combination is the specific hard prohibition in
# docs/decision_record.md ("PEP + geopolitical -- hard prohibition").
BANNED_TERMS = ("sanction", "pep", "politically exposed", "money laundering", "terroris")


@dataclass
class DomainAgentResult:
    prty_id: str
    domain: str
    tool_evidence: dict
    observed_facts: str
    hypothesis: str
    suggested_action: str
    caveats: str
    narrative_source: str
    latency_seconds: float = 0.0
    prompt_version: int | None = None

    @property
    def fell_back(self) -> bool:
        return self.narrative_source.startswith("deterministic_template")


def model_label(model) -> str:
    """Auditable model identifier for narrative_source -- e.g. the profile's local model tag,
    never an object repr with a memory address. The AgentCore governance
    table (docs/decision_record.md Tab 5, Phase 2) requires data lineage and
    an audit trail; '<OllamaModel object at 0x...>' satisfies neither."""
    try:
        config = model.get_config() or {}
        if isinstance(config, dict) and config.get("model_id"):
            return str(config["model_id"])
    except Exception:  # noqa: BLE001 -- labelling must never break a run
        pass
    return type(model).__name__


def _pct(value) -> str:
    return f"{float(value) * 100:.1f}%"


def _money(value, e: dict | None = None) -> str:
    """R17: formatted in the evidence's own currency, never an assumed EUR."""
    cur = str((e or {}).get("currency", "EUR")).upper()
    return f"{cur} {float(value):,.0f}"


# One plain-English sentence per deterministic tool -- used by the
# fallback path, so an RM reading a template-fallback row still gets
# something they can repeat to a colleague. Every figure comes straight
# from the tool's own evidence dict; nothing is inferred.
EVIDENCE_SENTENCES = {
    "check_cash_buildup": lambda e: (
        f"Deposit balance rose {_pct(e['increase_pct'])} to {_money(e['current_balance'], e)} "
        f"(from {_money(e['prior_balance'], e)}) as at {e['event_date']}."),
    "check_large_incoming_payment": lambda e: (
        f"Incoming payment of {_money(e['flagged_amount'], e)} on {e['event_date']} against a "
        f"{_money(e['baseline_median'], e)} median over {e['baseline_n']} prior credits."),
    "check_dormancy": lambda e: (
        f"No account activity for {e['days_since_last_event']} days "
        f"(last transaction {e['last_event_date']})."),
    "check_revenue_pattern_change": lambda e: (
        f"Average incoming payment changed {_pct(e['change_pct'])}, from {_money(e['prior_mean_amount'], e)} "
        f"to {_money(e['recent_mean_amount'], e)}, as at {e['event_date']}."),
    "check_facility_utilization": lambda e: (
        f"Facility {_pct(e['utilization_pct'])} drawn ({_money(e['drawn_amount'], e)} of "
        f"{_money(e['orig_limit'], e)}) as at {e['event_date']}."),
    "check_facility_maturity": lambda e: (
        f"Facility matures {e['close_date']}, in {e['days_to_close']} days."),
    "check_fixed_rate_expiry": lambda e: (
        f"Fixed-rate period ends {e['fixed_rate_end_date']}, in {e['days_to_expiry']} days."),
    "check_collateral_coverage": lambda e: (
        f"Collateral cover fell to {_pct(e['current_coverage_pct'])} of the facility limit "
        f"(previously {_pct(e['prior_max_coverage_pct'])})."),
    "check_exogenous_exposure": lambda e: (
        f"Exposed to a {str(e['event_type']).replace('_', ' ')} on {e['event_date']} "
        f"(sector {e['affected_sector']}, {e['affected_country']}, "
        f"value {_money(e['estimated_value_eur'], e)}), confirmed by the client's own activity."),
    "check_rating_downgrade": lambda e: (
        f"Credit risk grade worsened from {e['prior_grade_cd']} to {e['current_grade_cd']} "
        f"({e['notches']} notch(es)) as at {e['event_date']}."),
}


def describe_evidence(tool_name: str, evidence: dict) -> str:
    """Readable sentence for one tool's evidence. Unknown tools (a domain
    added later without a sentence registered) degrade to labelled
    key/value pairs -- still readable, never a dict repr."""
    template = EVIDENCE_SENTENCES.get(tool_name)
    if template is not None:
        try:
            return template(evidence)
        except (KeyError, TypeError, ValueError):
            pass  # evidence shape changed -- fall through rather than crash the fallback
    pairs = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in evidence.items()
                      if k not in ("status", "evidence_ref"))
    return f"{tool_name.replace('check_', '').replace('_', ' ').capitalize()}: {pairs}."


def _extract_numbers(text: str) -> list[float]:
    return [float(n.replace(",", "")) for n in re.findall(r"[\d,]+\.?\d*", text)
            if n.replace(",", "").replace(".", "").isdigit()]


def _numeric_tolerance_ok(text: str, tool_evidence: dict) -> bool:
    """At least one number stated in the narrative must correspond
    (within 1%, or exact for percentages/small integers) to some numeric
    value actually returned by a tool -- same discipline as
    ollama_narrator.py's flagged_amount check, generalized to 'any
    detector's evidence dict' rather than one fixed field name."""
    reference_numbers = []
    for evidence in tool_evidence.values():
        if not isinstance(evidence, dict):
            continue
        for k, v in evidence.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                reference_numbers.append(float(v))
                # Ratios are stored as fractions (change_pct=0.7683) but any
                # competent narrative -- and any RM -- says "76.83%". Only
                # fields that genuinely ARE percentages (the `_pct` naming
                # convention every detector follows) are rescaled. A live run
                # showed why the restriction matters: rescaling everything let
                # "76.83% exposure" validate against exposure_magnitude=0.7683,
                # which is a 0..1 score, not a percentage -- a false claim an
                # RM could repeat to a client.
                if k.endswith("_pct") and 0 < abs(v) <= 1:
                    reference_numbers.append(float(v) * 100)
    if not reference_numbers:
        return True  # nothing numeric to check against (e.g. all not_detected)
    found = _extract_numbers(text)
    if not found:
        return False
    for f in found:
        for r in reference_numbers:
            tol = max(0.01, abs(r) * 0.01)
            if abs(f - r) <= tol:
                return True
    return False


# Signed change fields -- a positive value means the measure went UP.
# Level fields (utilization_pct, current_coverage_pct) are deliberately
# excluded: "fell" is a correct word for a coverage drop.
SIGNED_CHANGE_KEYS = ("change_pct", "increase_pct")
DECREASE_WORDS = ("decrease", "declin", "fell", "fall", "drop", "lower", "reduc", "shrank")
INCREASE_WORDS = ("increase", "rose", "rise", "grew", "growth", "higher", "climb", "up ")

# R17: every tool's evidence carries its account's `currency`;
# _currency_problem rejects a narrative that names a currency the
# evidence does not carry -- in either direction (EUR narrated on USD
# evidence is as wrong as the reverse).
CURRENCY_MARKERS = {"EUR": ("€", "eur", "euro"), "USD": ("$", "usd", "us dollar"), "GBP": ("£", "gbp", "sterling")}


def _direction_problem(text: str, tool_evidence: dict) -> str | None:
    """A positive change narrated with only decrease words is a factual
    inversion that the number check alone cannot see."""
    lowered = text.lower()
    has_decrease = any(w in lowered for w in DECREASE_WORDS)
    has_increase = any(w in lowered for w in INCREASE_WORDS)
    for name, evidence in tool_evidence.items():
        if not isinstance(evidence, dict) or evidence.get("status") != "detected":
            continue
        for key in SIGNED_CHANGE_KEYS:
            value = evidence.get(key)
            if isinstance(value, (int, float)) and value > 0 and has_decrease and not has_increase:
                return f"narrative describes a decrease but {name}.{key} is +{value:.4f} (direction contradiction)"
    return None


def _currency_problem(text: str, tool_evidence: dict) -> str | None:
    evidence_currencies = {
        str(e.get("currency", "EUR")).upper()
        for e in tool_evidence.values() if isinstance(e, dict)
    } or {"EUR"}
    lowered = text.lower()
    for currency, markers in CURRENCY_MARKERS.items():
        if currency not in evidence_currencies and any(m in lowered for m in markers):
            return f"narrative states {currency} but evidence amounts are {sorted(evidence_currencies)} (currency mismatch)"
    return None


class DomainAgent:
    def __init__(self, domain: str, tools: list, model):
        try:
            self.allowed_actions = _allowed_actions(domain)
        except UnknownDomainError:
            raise ValueError(f"unknown domain: {domain}")
        self.domain = domain
        self.tools = tools
        self.model = model
        template, self.prompt_version = load_prompt("domain_agent")
        self.system_prompt = template.format(domain=domain, allowed_actions=list(self.allowed_actions))

    def _gather_tool_evidence(self, prty_id: str) -> dict:
        """Call every tool directly (no LLM) -- used both to ground the
        agent's prompt and as the deterministic fallback path."""
        return {t.tool_name: t(prty_id) for t in self.tools}

    def _validate(self, parsed: DomainNarrative, tool_evidence: dict) -> list[str]:
        problems = []
        for field in ("observed_facts", "hypothesis", "suggested_action", "caveats"):
            value = getattr(parsed, field)
            if not value or not value.strip():
                problems.append(f"missing or empty field: {field}")
        if parsed.suggested_action not in self.allowed_actions:
            problems.append(f"suggested_action not in allowed set: {parsed.suggested_action!r}")
        text_blob = (parsed.observed_facts + " " + parsed.hypothesis).lower()
        for banned in BANNED_TERMS:
            if banned in text_blob:
                problems.append(f"narrative used a disallowed term: '{banned}'")
        if not _numeric_tolerance_ok(parsed.observed_facts, tool_evidence):
            problems.append("no number in observed_facts matches any tool evidence value")
        narrative = parsed.observed_facts + " " + parsed.hypothesis
        for check in (_direction_problem, _currency_problem):
            problem = check(narrative, tool_evidence)
            if problem:
                problems.append(problem)
        any_detected = any(
            isinstance(e, dict) and e.get("status") == "detected" for e in tool_evidence.values()
        )
        if not any_detected and parsed.suggested_action != "No action -- monitor only":
            problems.append("no tool detected anything, but suggested_action is not 'monitor only'")
        return problems

    def _fallback(self, prty_id: str, tool_evidence: dict, reason: str, latency_seconds: float = 0.0
                  ) -> DomainAgentResult:
        detected = {k: v for k, v in tool_evidence.items() if isinstance(v, dict) and v.get("status") == "detected"}
        if not detected:
            facts = "No signals detected by any deterministic check in this domain for this client."
            action = "No action -- monitor only"
        else:
            # The fallback is what an RM reads whenever the LLM output fails
            # validation -- it must be a sentence, never a raw dict dump.
            facts = " ".join(describe_evidence(name, evidence) for name, evidence in detected.items())
            action = self.allowed_actions[0]
        return DomainAgentResult(
            prty_id=prty_id, domain=self.domain, tool_evidence=tool_evidence,
            observed_facts=facts,
            hypothesis="Deterministic template -- see tool_evidence for the underlying signal(s).",
            suggested_action=action,
            caveats="Synthetic POC output for RM review, not a validated business conclusion. "
                    f"LLM narrative unavailable this run: {reason}",
            narrative_source=f"deterministic_template ({self.domain} agent fallback: {reason})",
            latency_seconds=latency_seconds, prompt_version=self.prompt_version,
        )

    def evaluate(self, prty_id: str) -> DomainAgentResult:
        import time

        t0 = time.monotonic()
        tool_evidence = self._gather_tool_evidence(prty_id)
        try:
            from strands import Agent

            # Ground the model in the deterministic facts already computed.
            # A live run showed an ungrounded model inverting "from prior to
            # recent" and relabelling EUR as "$" while every number still
            # matched -- giving it the canonical sentences makes the faithful
            # restatement the path of least resistance, and _validate still
            # checks whatever comes back.
            #
            # A0 (docs/agentic_plan.md): the agent gets NO tools here. Every
            # detector already ran once, deterministically, in
            # _gather_tool_evidence() above -- giving the model tool access
            # too meant the LLM re-ran the same tools itself (the system
            # prompt used to say "call every tool"), doubling detector work
            # and adding a tool-calling round trip for zero benefit, since
            # _validate() only ever checks against tool_evidence gathered
            # here, never against whatever the model's own calls returned.
            # The model now narrates the verified facts it's handed; it
            # cannot call anything.
            verified = [describe_evidence(n, e) for n, e in tool_evidence.items()
                        if isinstance(e, dict) and e.get("status") == "detected"]
            facts_block = "\n".join(f"- {s}" for s in verified) or "- No signals detected by any check."
            currencies = {str(e.get("currency", "EUR")).upper() for e in tool_evidence.values()
                          if isinstance(e, dict) and e.get("status") == "detected"} or {"EUR"}
            agent = Agent(model=self.model, tools=[], system_prompt=self.system_prompt)
            result = agent(
                f"Summarize client {prty_id}'s verified facts below.\n\n"
                f"Verified facts from the deterministic checks. Restate them faithfully: keep "
                f"every direction (from/to), every currency ({', '.join(sorted(currencies))}), and every figure exactly as "
                f"written. Do not describe a score as a percentage.\n{facts_block}",
                structured_output_model=DomainNarrative,
            )
            parsed = result.structured_output
            if parsed is None:
                return self._fallback(prty_id, tool_evidence, "agent returned no structured output",
                                       time.monotonic() - t0)
        except Exception as e:  # noqa: BLE001 -- any agent/model failure falls back, never raises
            return self._fallback(prty_id, tool_evidence, f"{type(e).__name__}: {e}", time.monotonic() - t0)

        problems = self._validate(parsed, tool_evidence)
        if problems:
            return self._fallback(prty_id, tool_evidence, "; ".join(problems), time.monotonic() - t0)

        return DomainAgentResult(
            prty_id=prty_id, domain=self.domain, tool_evidence=tool_evidence,
            observed_facts=parsed.observed_facts, hypothesis=parsed.hypothesis,
            suggested_action=parsed.suggested_action,
            caveats=parsed.caveats + " Synthetic POC output for RM review, not a validated business conclusion.",
            narrative_source=f"strands+ollama:{model_label(self.model)}",
            latency_seconds=time.monotonic() - t0, prompt_version=self.prompt_version,
        )
