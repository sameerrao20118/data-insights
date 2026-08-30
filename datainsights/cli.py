"""
Typed run-once entry point exposed via CLI. Same function local scheduler /
future service wrappers would call (project instructions section 4.1) --
this file is just the argument-parsing shell around datainsights.runner.
"""

from __future__ import annotations

import argparse
import json
from datetime import date

from datainsights.runner import run_once


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="offline_ollama")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date(2025, 12, 31),
                         help="Evaluate the dataset as-of this date (YYYY-MM-DD). "
                              "Only transactions on or before this date are visible.")
    parser.add_argument("--max-narratives", type=int, default=15,
                         help="Generate narratives for at most this many top-ranked "
                              "detections (local LLM calls are sequential, ~3-15s each).")
    parser.add_argument("--no-narratives", action="store_true")
    args = parser.parse_args()

    result = run_once(
        profile_name=args.profile,
        as_of=args.as_of,
        generate_narratives=not args.no_narratives,
        max_narratives=args.max_narratives,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
