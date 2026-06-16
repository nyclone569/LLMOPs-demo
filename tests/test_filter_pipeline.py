import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import _strip_fences, _validate_sql, SQLValidationError


def test_strip_fences_removes_sql_block():
    assert _strip_fences("```sql\nSELECT 1\n```") == "SELECT 1"


def test_strip_fences_removes_plain_block():
    assert _strip_fences("```\nSELECT 1\n```") == "SELECT 1"


def test_strip_fences_passthrough_plain_sql():
    assert _strip_fences("SELECT 1") == "SELECT 1"


def test_validate_sql_passes_valid_select():
    _validate_sql("SELECT trip_count FROM kpi_monthly_summary", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_ddl():
    with pytest.raises(SQLValidationError, match="DDL"):
        _validate_sql("SELECT 1; DROP TABLE kpi_monthly_summary", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_file_functions():
    with pytest.raises(SQLValidationError, match="file function"):
        _validate_sql("SELECT * FROM read_parquet('s3://...')", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_wrong_table():
    with pytest.raises(SQLValidationError, match="not allowed"):
        _validate_sql("SELECT * FROM some_other_table", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_non_select():
    with pytest.raises(SQLValidationError, match="SELECT"):
        _validate_sql("INSERT INTO foo VALUES (1)", "kpi_monthly_summary", {"kpi_monthly_summary"})


def test_validate_sql_rejects_unknown_expected_table():
    with pytest.raises(SQLValidationError, match="not in registry"):
        _validate_sql("SELECT 1 FROM bad_table", "bad_table", {"kpi_monthly_summary"})


def test_validate_sql_allows_cte():
    _validate_sql(
        "WITH cte AS (SELECT trip_count FROM kpi_monthly_summary) SELECT * FROM cte",
        "kpi_monthly_summary",
        {"kpi_monthly_summary"},
    )


@pytest.mark.asyncio
async def test_pipe_chat_returns_streaming_response():
    from filter_analytics import Pipe
    from starlette.responses import StreamingResponse

    pipe = Pipe()
    body = {"messages": [{"role": "user", "content": "explain linked lists"}]}

    async def fake_stream(messages, ollama_url, model):
        async def gen():
            yield b"data: {}"
        return StreamingResponse(gen(), media_type="text/event-stream")

    with patch("filter_analytics._stream_ollama", side_effect=fake_stream):
        result = await pipe.pipe(body)

    assert isinstance(result, StreamingResponse)


@pytest.mark.asyncio
async def test_pipe_analytics_emits_status_events():
    from filter_analytics import Pipe

    pipe = Pipe()
    body = {"messages": [{"role": "user", "content": "show monthly revenue trend for taxi trips"}]}

    emitted = []

    async def mock_emitter(event):
        emitted.append(event)

    with patch("filter_analytics._run_analytics", return_value="summary text"):
        await pipe.pipe(body, __event_emitter__=mock_emitter)

    assert len(emitted) == 2
    assert emitted[0] == {"type": "status", "data": {"description": "Analyzing", "done": False}}
    assert emitted[1] == {"type": "status", "data": {"description": "Analyzing", "done": True}}


@pytest.mark.asyncio
async def test_pipe_analytics_skips_emitter_when_none():
    from filter_analytics import Pipe

    pipe = Pipe()
    body = {"messages": [{"role": "user", "content": "show monthly revenue trend for taxi trips"}]}

    with patch("filter_analytics._run_analytics", return_value="summary text"):
        result = await pipe.pipe(body, __event_emitter__=None)

    assert result == "summary text"
