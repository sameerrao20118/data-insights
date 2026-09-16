"""
Cross-domain correlation (docs/decision_record.md Tab 3, "Layer 3 --
Correlation (the differentiator)"): Signal Bus -> Hypothesis Assembler ->
De-duplication. Plain deterministic Python throughout -- this is the one
layer the decision record is explicit must never be an LLM decision
("correlation is a deterministic rules engine; LLM only extracts and
narrates" -- CLAUDE.md / docs/decision_record.md non-negotiable
constraints).

This is the single biggest genuinely new capability this build adds --
nothing like it existed before this session (docs/gap_analysis.md Gaps 2
& 3: "nothing assembles evidence across deposits, lending, treasury and
risk into one confidence-weighted hypothesis").
"""
