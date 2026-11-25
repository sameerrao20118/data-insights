"""
The AWS read path, proven without AWS (docs/aws_deployment.md).

On AWS the agents read the curated lake through Athena: the Glue 5.1 job
(infra/glue/curate_domain.py) reads each raw CSV as strings, casts every
contracted column to the type in lake_tables.json, lower-cases the names
(Glue does), drops uncontracted columns, and writes one Glue database per
contract domain. Here DuckDB stands in for Athena with data loaded the
SAME way, and the whole book must produce identical recommendations
through SqlSource as through FdmLocalSource. Real Athena stays NOT RUN.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import timedelta

import duckdb
import pytest

from agents.orchestrator import evaluate_book
from datainsights.correlation.dedupe import dedupe
from datainsights.deploy.lake_contract import DEFAULT_OUT, render
from datainsights.runtime import build_runtime
from datainsights.semantic.binding import load_binding
from datainsights.semantic.canonical import CanonicalSource
from datainsights.sources.sql_source import SqlSource
from external_events.exposure_qualifier import load_events

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FDM_DIR = os.path.join(REPO_ROOT, "data_generator", "output_fdm")
CONTRACT = os.path.join(REPO_ROOT, "config", "entities_fdm.yaml")
DUCK_TYPES = {"string": "VARCHAR", "date": "DATE", "double": "DOUBLE", "bigint": "BIGINT"}


def test_committed_lake_spec_matches_the_contract():
    """Terraform and the Glue job read the committed JSON; it must be what
    the contract says today. Fix: python -m datainsights.deploy.lake_contract"""
    with open(DEFAULT_OUT) as f:
        assert f.read() == render(CONTRACT)


def test_lake_spec_never_contains_protected_or_fincrime_tables():
    spec = json.loads(render(CONTRACT))
    names = {t["table"] for t in spec["tables"]}
    assert not any("protected" in n or "trigger" in n or n.endswith("_xdo") for n in names)


def _load_like_glue(con, spec: dict) -> None:
    for domain in spec["domains"]:
        con.execute(f'CREATE SCHEMA "{domain}"')
    for t in spec["tables"]:
        path = os.path.join(FDM_DIR, t["domain"], f"{t['table']}.csv")
        header = con.execute(f"SELECT * FROM read_csv('{path}', all_varchar=true) LIMIT 0").fetchdf().columns
        select = []
        for c in t["columns"]:
            src = c["source_name"]
            expr = f"CAST(NULLIF(\"{src}\", '') AS {DUCK_TYPES[c['type']]})" if src in header else f"CAST(NULL AS {DUCK_TYPES[c['type']]})"
            select.append(f'{expr} AS "{c["name"]}"')
        con.execute(f'CREATE TABLE "{t["domain"]}"."{t["table"]}" AS SELECT {", ".join(select)} '
                    f"FROM read_csv('{path}', all_varchar=true)")


@pytest.mark.skipif(not os.path.isdir(FDM_DIR), reason="FDM data not generated")
def test_whole_book_is_identical_through_the_lake_shaped_sql_source():
    spec = json.loads(render(CONTRACT))
    con = duckdb.connect(database=":memory:")
    _load_like_glue(con, spec)
    lake = SqlSource("duckdb", CONTRACT, connection_factory=lambda: con,
                     domain_schema_map={d: d for d in spec["domains"]}, statement_row_limit=500_000)

    rt = build_runtime("fdm_local")
    event = load_events(rt.event_source_path)[0]
    as_of = event.event_date + timedelta(days=90)
    ids = sorted(CanonicalSource(rt.source, load_binding("fdm")).read("Party", as_at=as_of)["party_id"])
    assert ids == sorted(CanonicalSource(lake, load_binding("fdm")).read("Party", as_at=as_of)["party_id"])

    def recs(source):
        book = evaluate_book(ids, source=source, rules=rt.rules, as_of=as_of, event=event, binding_name="fdm")
        assert not [e.error for e in book if e.error]
        return sorted(json.dumps(asdict(r), sort_keys=True, default=str)
                      for r in dedupe([e.recommendation for e in book if e.recommendation]))

    local, via_lake = recs(rt.source), recs(lake)
    assert len(local) > 0
    assert via_lake == local
