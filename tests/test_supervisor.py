import json
import pytest
from unittest.mock import patch
from pathlib import Path
from analytics_agent.agents.supervisor import run_supervisor, SupervisorResult, SupervisorError

REGISTRY = json.loads((Path(__file__).parent / "fixtures" / "schema_registry.json").read_text())


def _mock_chat(content: str):
    return lambda messages, **_: content


def test_supervisor_returns_high_confidence():
    output = json.dumps({
        "table": "kpi_monthly_summary",
        "confidence": "high",
        "reasoning": "monthly revenue question",
    })
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        result = run_supervisor("show monthly revenue trend", REGISTRY)
    assert result.table == "kpi_monthly_summary"
    assert result.confidence == "high"


def test_supervisor_returns_low_confidence():
    output = json.dumps({
        "table": "dim_zone",
        "confidence": "low",
        "reasoning": "ambiguous question",
    })
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        result = run_supervisor("show something", REGISTRY)
    assert result.confidence == "low"


def test_supervisor_treats_unknown_confidence_as_low():
    output = json.dumps({
        "table": "kpi_monthly_summary",
        "confidence": "medium",
        "reasoning": "somewhat sure",
    })
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        result = run_supervisor("something", REGISTRY)
    assert result.confidence == "low"
    assert result.unexpected_confidence == "medium"


def test_supervisor_raises_on_unknown_table():
    output = json.dumps({
        "table": "nonexistent_table",
        "confidence": "high",
        "reasoning": "picked a bad table",
    })
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        with pytest.raises(SupervisorError, match="not in registry"):
            run_supervisor("something", REGISTRY)


def test_supervisor_raises_on_invalid_json():
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat("not json at all")):
        with pytest.raises(SupervisorError, match="parse"):
            run_supervisor("something", REGISTRY)


def test_supervisor_raises_on_json_wrapped_in_markdown():
    # Model sometimes wraps JSON in ```json blocks — supervisor should handle or raise clearly
    wrapped = '```json\n{"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "ok"}\n```'
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(wrapped)):
        # Either parse it successfully by stripping fences, or raise a clear SupervisorError
        # We accept either — just must not throw an unhandled exception
        try:
            result = run_supervisor("show revenue", REGISTRY)
            assert result.table == "kpi_monthly_summary"
        except SupervisorError:
            pass  # also acceptable
