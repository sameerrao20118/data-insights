#!/usr/bin/env bash
# One-command full simulation: verify -> detect -> rank -> narrate -> digest,
# across both event categories, ending in a single unified worklist.
# This is literally "python -m ..." x5 in sequence -- nothing here is new
# logic, it's the reproducible path through what already exists.
set -e
cd "$(dirname "$0")"
source .venv/bin/activate

echo "=================================================================="
echo "1/5  Test suite -- proves the detection logic behaves as designed"
echo "=================================================================="
python -m pytest tests/ -q

echo
echo "=================================================================="
echo "2/5  Endogenous pipeline: transaction anomaly detection"
echo "=================================================================="
python -m datainsights.cli --max-narratives 15

echo
echo "=================================================================="
echo "3/5  Verification against embedded ground truth (dev diagnostic --"
echo "     see the printed caveat; this is NOT a clean external benchmark)"
echo "=================================================================="
python -m evaluation.evaluate

echo
echo "=================================================================="
echo "4/5  Exogenous pipeline: market/industry/political event matching"
echo "=================================================================="
python -m external_events.demo_scenario

echo
echo "=================================================================="
echo "5/5  Unified RM worklist -- one row per client recommendation"
echo "=================================================================="
python -m datainsights.build_worklist

echo
echo "=================================================================="
echo "Done. Outputs to look at:"
echo "  var/insights/digest_*.md      -- narrative digests, most recent two"
echo "  var/insights/worklist_*.csv   -- the unified worklist, most recent"
echo "=================================================================="
ls -t var/insights/digest_*.md | head -2
ls -t var/insights/worklist_*.csv | head -1
