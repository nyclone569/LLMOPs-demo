import json
import pytest
from unittest.mock import patch
from pathlib import Path
from analytics_agent.agents.summarize import run_summarize_agent, SummarizeResult, SummarizeError

ROWS = json.loads((Path(__file__).parent / "fixtures" / "sample_rows.json").read_text())


def _mock_chat(content: str):
    return lambda messages, **_: content


def test_summarize_returns_summary_and_chart():
    output = json.dumps({
        "summary": "January was the peak revenue month.",
        "chart_spec": {"type": "bar", "x": "month", "y": "revenue", "series": []},
        "capped": False,
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show monthly revenue", ROWS, capped=False)
    assert "January" in result.summary
    assert result.chart_spec["type"] == "bar"


def test_summarize_notes_cap_in_output():
    output = json.dumps({
        "summary": "Results limited to 200 rows.",
        "chart_spec": {"type": "table", "x": "month", "y": "revenue", "series": []},
        "capped": True,
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show everything", ROWS, capped=True)
    assert result.capped is True


def test_summarize_raises_on_invalid_json():
    with patch("analytics_agent.agents.summarize.chat", _mock_chat("not json")):
        with pytest.raises(SummarizeError, match="parse"):
            run_summarize_agent("show revenue", ROWS, capped=False)


def test_summarize_raises_on_empty_summary():
    output = json.dumps({
        "summary": "  ",
        "chart_spec": {"type": "bar", "x": "month", "y": "revenue", "series": []},
        "capped": False,
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        with pytest.raises(SummarizeError, match="empty summary"):
            run_summarize_agent("show revenue", ROWS, capped=False)


def test_summarize_drops_chart_on_missing_column():
    output = json.dumps({
        "summary": "Good summary.",
        "chart_spec": {"type": "bar", "x": "nonexistent_col", "y": "revenue", "series": []},
        "capped": False,
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show revenue", ROWS, capped=False)
    assert result.chart_spec is None
    assert result.chart_invalid_reason is not None


def test_summarize_rejects_invalid_chart_type():
    output = json.dumps({
        "summary": "Good summary.",
        "chart_spec": {"type": "scatter", "x": "month", "y": "revenue", "series": []},
        "capped": False,
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show revenue", ROWS, capped=False)
    assert result.chart_spec is None
    assert result.chart_invalid_reason is not None
    assert "scatter" in result.chart_invalid_reason


def test_summarize_handles_empty_rows():
    output = json.dumps({
        "summary": "No data found.",
        "chart_spec": {"type": "bar", "x": "month", "y": "revenue", "series": []},
        "capped": False,
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show revenue", [], capped=False)
    assert result.summary == "No data found."
    assert result.chart_spec is None  # no rows to chart
