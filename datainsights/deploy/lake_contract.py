"""
The source contract (config/entities_fdm.yaml) as a lake table spec --
ONE derivation shared by everything that shapes data on AWS:

  * infra/terraform/stacks/20-data-mesh reads lake_tables.json to create
    the Glue Data Catalog tables, the Glue Data Quality rulesets and the
    Lake Formation tags, one Glue database per contract `domain:`;
  * infra/glue/curate_domain.py (the Glue 5.1 job) casts raw CSV to
    exactly these types and drops every uncontracted column;
  * tests/test_aws_read_path.py loads the local CSVs the same way and
    proves the whole pipeline gives identical recommendations through
    SqlSource as through FdmLocalSource.

Regenerate after editing the contract:
    python -m datainsights.deploy.lake_contract
CI fails if the committed JSON is stale (tests/test_aws_read_path.py).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CONTRACT = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
DEFAULT_OUT = os.path.join(REPO_ROOT, "infra", "terraform", "stacks", "20-data-mesh", "lake_tables.json")

# contract type -> Athena/Glue type. decimal -> double, not decimal(p,s):
# the contract declares no precision, and pandas reads both as float.
ATHENA_TYPES = {"string": "string", "date": "date", "decimal": "double", "int": "bigint"}


def lake_tables(contract_path: str = DEFAULT_CONTRACT) -> dict:
    with open(contract_path) as f:
        contract = yaml.safe_load(f)
    tables = []
    for entity, spec in contract["entities"].items():
        cols = []
        for nullable_default, block in ((False, "required_columns"), (True, "optional_columns")):
            for name, col in (spec.get(block) or {}).items():
                ctype = (col or {}).get("type", "string")
                if ctype not in ATHENA_TYPES:
                    raise ValueError(f"{entity}.{name}: contract type {ctype!r} has no lake mapping")
                cols.append({"name": name.lower(), "source_name": name, "type": ATHENA_TYPES[ctype],
                             "nullable": bool((col or {}).get("nullable", nullable_default))})
        tables.append({
            "entity": entity,
            "table": spec["physical_table"].lower(),
            "domain": spec["domain"],
            "primary_key": [c.lower() for c in spec.get("primary_key", [])],
            "event_time_field": ((spec.get("time_semantics") or {}).get("event_time_field") or "").lower() or None,
            "columns": cols,
        })
    return {"generated_from": os.path.relpath(contract_path, REPO_ROOT),
            "domains": sorted({t["domain"] for t in tables}),
            "tables": tables}


def render(contract_path: str = DEFAULT_CONTRACT) -> str:
    return json.dumps(lake_tables(contract_path), indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--contract", default=DEFAULT_CONTRACT)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="exit 1 if --out is stale")
    args = parser.parse_args()
    text = render(args.contract)
    if args.check:
        current = open(args.out).read() if os.path.exists(args.out) else ""
        sys.exit(0 if current == text else 1)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write(text)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
