import json
import pytest
from unittest.mock import patch
from pathlib import Path
from analytics_agent.pipeline import run_pipeline, PipelineResult

REGISTRY = json.loads((Path(__file__).parent / "fixtures" / "schema_registry.json").read_text())


def _make_supervisor(table="kpi_monthly_summary", confidence="high"):
    from analytics_agent.agents.supervisor import SupervisorResult
    return SupervisorResult(
        table=table, confidence=confidence, reasoning="ok", raw_response="{}"
    )


def _make_query(rows=None):
    from analytics_agent.agents.query import QueryResult
    return QueryResult(
        sql="SELECT 1",
        rows=rows if rows is not None else [{"month": "2026-01", "revenue": 100.0}],
        capped=False,
        row_count_before_cap=1,
    )


def _make_summarize():
    from analytics_agent.agents.summarize import SummarizeResult
    return SummarizeResult(
        summary="Good summary.",
        chart_spec={"type": "bar", "x": "month", "y": "revenue", "series": []},
        capped=False,
    )


def test_pipeline_happy_path():
    with patch("analytics_agent.pipeline.run_supervisor", return_value=_make_supervisor()), \
         patch("analytics_agent.pipeline.run_query_agent", return_value=_make_query()), \
         patch("analytics_agent.pipeline.run_summarize_agent", return_value=_make_summarize()):
        result = run_pipeline("show monthly revenue", REGISTRY)
    assert result.summary == "Good summary."
    assert result.correlation_id is not None
    assert result.error is None


def test_pipeline_stops_on_low_confidence():
    with patch("analytics_agent.pipeline.run_supervisor", return_value=_make_supervisor(confidence="low")):
        result = run_pipeline("vague question", REGISTRY)
    assert result.clarification is not None
    assert result.summary is None
    assert result.error is None


def test_pipeline_returns_no_data_on_empty_rows():
    with patch("analytics_agent.pipeline.run_supervisor", return_value=_make_supervisor()), \
         patch("analytics_agent.pipeline.run_query_agent", return_value=_make_query(rows=[])):
        result = run_pipeline("ancient data", REGISTRY)
    assert result.summary == "No data found for this period."
    assert result.chart_spec is None


def test_pipeline_log_has_correlation_id():
    with patch("analytics_agent.pipeline.run_supervisor", return_value=_make_supervisor()), \
         patch("analytics_agent.pipeline.run_query_agent", return_value=_make_query()), \
         patch("analytics_agent.pipeline.run_summarize_agent", return_value=_make_summarize()):
        result = run_pipeline("show monthly revenue", REGISTRY)
    assert result.log["correlation_id"] == result.correlation_id


def test_pipeline_returns_error_on_ollama_unavailable():
    from analytics_agent.ollama_client import OllamaError
    with patch("analytics_agent.pipeline.run_supervisor", side_effect=OllamaError("timed out")):
        result = run_pipeline("show monthly revenue", REGISTRY)
    assert result.error is not None
    assert "timed out" in result.error
    assert result.summary is None
    assert result.log.get("outcome") == "error"


def test_pipeline_log_has_supervisor_info():
    with patch("analytics_agent.pipeline.run_supervisor", return_value=_make_supervisor()), \
         patch("analytics_agent.pipeline.run_query_agent", return_value=_make_query()), \
         patch("analytics_agent.pipeline.run_summarize_agent", return_value=_make_summarize()):
        result = run_pipeline("show monthly revenue", REGISTRY)
    assert result.log["supervisor"]["table_selected"] == "kpi_monthly_summary"
    assert result.log["supervisor"]["confidence"] == "high"


def test_pipeline_exposes_rows():
    with patch("analytics_agent.pipeline.run_supervisor", return_value=_make_supervisor()), \
         patch("analytics_agent.pipeline.run_query_agent", return_value=_make_query()), \
         patch("analytics_agent.pipeline.run_summarize_agent", return_value=_make_summarize()):
        result = run_pipeline("show monthly revenue", REGISTRY)
    assert len(result.rows) == 1
    assert result.rows[0]["revenue"] == 100.0
