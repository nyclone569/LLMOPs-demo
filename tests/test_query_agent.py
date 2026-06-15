import json
import pytest
from unittest.mock import patch
from pathlib import Path
from analytics_agent.agents.query import run_query_agent, QueryResult, QueryError

REGISTRY = json.loads((Path(__file__).parent / "fixtures" / "schema_registry.json").read_text())
KNOWN_TABLES = set(REGISTRY.keys())


def _mock_chat(sql: str):
    return lambda messages, **_: sql


def _mock_duckdb_rows():
    return [{"month": "2026-01-01", "revenue": 1234.5, "trip_count": 100, "avg_fare": 12.3}]


def test_query_agent_returns_rows():
    sql = "SELECT month, revenue FROM kpi_monthly_summary"
    with patch("analytics_agent.agents.query.chat", _mock_chat(sql)), \
         patch("analytics_agent.agents.query._execute_duckdb", return_value=_mock_duckdb_rows()):
        result = run_query_agent("show monthly revenue", "kpi_monthly_summary", REGISTRY, KNOWN_TABLES)
    assert result.rows[0]["revenue"] == 1234.5
    assert result.capped is False


def test_query_agent_caps_at_row_limit():
    sql = "SELECT month, revenue FROM kpi_monthly_summary"
    big_rows = [{"month": "2026-01-01", "revenue": float(i)} for i in range(300)]
    with patch("analytics_agent.agents.query.chat", _mock_chat(sql)), \
         patch("analytics_agent.agents.query._execute_duckdb", return_value=big_rows):
        result = run_query_agent("show monthly revenue", "kpi_monthly_summary", REGISTRY, KNOWN_TABLES)
    assert len(result.rows) == 200
    assert result.capped is True


def test_query_agent_raises_on_invalid_sql():
    with patch("analytics_agent.agents.query.chat", _mock_chat("DROP TABLE kpi_monthly_summary")):
        with pytest.raises(QueryError, match="validation"):
            run_query_agent("drop everything", "kpi_monthly_summary", REGISTRY, KNOWN_TABLES)


def test_query_agent_returns_empty_rows():
    sql = "SELECT month FROM kpi_monthly_summary WHERE month = '1900-01-01'"
    with patch("analytics_agent.agents.query.chat", _mock_chat(sql)), \
         patch("analytics_agent.agents.query._execute_duckdb", return_value=[]):
        result = run_query_agent("ancient data", "kpi_monthly_summary", REGISTRY, KNOWN_TABLES)
    assert result.rows == []
    assert result.capped is False


def test_query_agent_strips_markdown_fences():
    sql_wrapped = "```sql\nSELECT month FROM kpi_monthly_summary\n```"
    with patch("analytics_agent.agents.query.chat", _mock_chat(sql_wrapped)), \
         patch("analytics_agent.agents.query._execute_duckdb", return_value=_mock_duckdb_rows()):
        result = run_query_agent("show months", "kpi_monthly_summary", REGISTRY, KNOWN_TABLES)
    assert result.rows is not None


def test_query_agent_raises_on_duckdb_error():
    sql = "SELECT month FROM kpi_monthly_summary"
    with patch("analytics_agent.agents.query.chat", _mock_chat(sql)), \
         patch("analytics_agent.agents.query._execute_duckdb", side_effect=Exception("DuckDB exploded")):
        with pytest.raises(QueryError, match="DuckDB"):
            run_query_agent("show months", "kpi_monthly_summary", REGISTRY, KNOWN_TABLES)
