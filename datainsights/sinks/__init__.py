"""
Output sinks (docs/decision_record.md Tab 6, "Slot Type D -- output
channel"). The existing dashboard (dashboard/app.py) and worklist CSV
(datainsights/worklist.py) are this project's real, usable local output
today -- see agents/README.md's note that these correspond to
`WorklistCsvSink()`/`StreamlitSink()`, labelled DEMO ONLY in the decision
record. `mimo_placeholder.py` is additive: it emits the real MIMO insight
record + ODS packet SHAPE so that mapping exists and is tested, without
claiming to call the real MIMO platform (D1/D2's actual production route,
which this repo has no access to build against).
"""
