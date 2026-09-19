#!/usr/bin/env bash
# One-command demo: guards -> whole-book run on three schemas through the ONE
# pipeline (R23) -> the RM worklist. Nothing here is new logic.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

echo "1/4  Lint + deterministic suite"
python -m ruff check .
python -m pytest tests/ -q -k "not live"

echo "2/4  Whole book, FDM synthetic schema (tender-award exogenous event confirmed per client)"
python -m agents.demo_fdm_scenario

echo "3/4  Same pipeline, legacy schema (large_incoming_payment fires here)"
python -m agents.demo_fdm_scenario --profile legacy_local

echo "4/4  Same pipeline, real SBA entities"
python -m agents.demo_fdm_scenario --profile sba_local

echo "Worklist: var/insights/fdm_rm_worklist.csv  --  digest: var/insights/fdm_rm_digest.md"
