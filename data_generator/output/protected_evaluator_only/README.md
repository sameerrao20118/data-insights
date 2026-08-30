# Evaluator-only — do not read from detector or coding-assistant context

`trigger_events.csv` in this directory is ground-truth for EVALUATING a
detector, not an input to one. It must never be:

- read by detection_engine/ code,
- read by a coding-assistant session working on detection_engine/,
- loaded into any Snowflake schema/role that detector code has access to.

This directory boundary is a convenience, not an access-control guarantee.
Real isolation requires a separate Snowflake schema/role (or separate
storage entirely) that only an evaluator identity can read — see the
project instructions, section 3 ("Protect the evaluation before repository
discovery").

Known contamination: the coding session that authored
`data_generator/generate_data.py` (this repository's synthetic dataset)
also wrote the trigger-injection logic and has directly inspected this
file's contents. That session's development-time precision/recall numbers
against this file are development diagnostics only, not a clean holdout
benchmark. A legitimate final evaluation needs a separately generated
dataset that the detector-building session never inspects.
