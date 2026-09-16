"""
Strands-based domain agents (docs/decision_record.md's Agent Signal Map:
"domain-specialist agents read these signals, correlate them with
exogenous events... the intersection of multiple domain views on the same
client"), reconciled with the record's own non-negotiable constraints
("no autonomous multi-step agent"; "correlation is a deterministic rules
engine; LLM only extracts and narrates").

Resolution (agreed with the user this session): agents are TOOL-CALLING,
not autonomous. Each domain agent calls deterministic detectors
(detection_engine/) as Strands tools, assembles that domain's evidence,
and narrates it -- with the exact validate-or-fallback discipline already
proven in datainsights/narrative/ollama_narrator.py. Cross-domain
correlation stays outside any agent, in datainsights/correlation/ (M6) --
plain deterministic Python, never an LLM decision.
"""
