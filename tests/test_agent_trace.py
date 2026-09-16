"""
A0 verification: agent execution traces are recorded, queryable by
recommendation_id, and DomainAgentResult.fell_back correctly reflects
whether the LLM narrative was used or the deterministic template was.
"""

from datainsights.agent_trace import AgentTrace, connect, record, traces_for_recommendation


def test_record_and_query_by_recommendation_id(tmp_path):
    db_path = str(tmp_path / "traces.db")
    with connect(db_path) as con:
        record(con, AgentTrace(prty_id="P1", domain="deposits", narrative_source="strands+ollama:qwen2.5:7b",
                                latency_seconds=1.5, fell_back=False, recommendation_id="rec1"))
        record(con, AgentTrace(prty_id="P1", domain="lending", narrative_source="deterministic_template (x)",
                                latency_seconds=0.2, fell_back=True, recommendation_id="rec1"))
        record(con, AgentTrace(prty_id="P2", domain="deposits", narrative_source="strands+ollama:qwen2.5:7b",
                                latency_seconds=2.1, fell_back=False, recommendation_id="rec2"))

    with connect(db_path) as con:
        rows = traces_for_recommendation(con, "rec1")
    assert len(rows) == 2
    assert {r["domain"] for r in rows} == {"deposits", "lending"}
    assert rows[0]["fell_back"] in (0, 1)


def test_trace_survives_no_recommendation(tmp_path):
    """A domain narrated but the client ended up with no recommendation
    at all (assemble() returned None) -- the trace must still record,
    with recommendation_id NULL, not silently dropped."""
    db_path = str(tmp_path / "traces.db")
    with connect(db_path) as con:
        record(con, AgentTrace(prty_id="P1", domain="deposits", narrative_source="strands+ollama:qwen2.5:7b",
                                latency_seconds=1.0, fell_back=False, recommendation_id=None))
    with connect(db_path) as con:
        cur = con.execute("SELECT recommendation_id FROM agent_traces")
        assert cur.fetchone()[0] is None


def test_domain_agent_result_fell_back_reflects_narrative_source():
    from agents.domain_agent import DomainAgentResult

    llm_result = DomainAgentResult(
        prty_id="P1", domain="deposits", tool_evidence={}, observed_facts="x", hypothesis="x",
        suggested_action="No action -- monitor only", caveats="x",
        narrative_source="strands+ollama:qwen2.5:7b",
    )
    fallback_result = DomainAgentResult(
        prty_id="P1", domain="deposits", tool_evidence={}, observed_facts="x", hypothesis="x",
        suggested_action="No action -- monitor only", caveats="x",
        narrative_source="deterministic_template (deposits agent fallback: some reason)",
    )
    assert llm_result.fell_back is False
    assert fallback_result.fell_back is True
