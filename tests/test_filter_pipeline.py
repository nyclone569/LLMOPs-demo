import pytest
import sqlite3
import sys
import time
import json
from pathlib import Path
from unittest.mock import patch, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import _strip_fences, _validate_sql, SQLValidationError, build_html_artifact, chart_spec_to_vegalite, _load_registry, _stream_summary, _stream_analytics


class _FakeHTTPResponse:
    def read(self):
        return b"""<AssumeRoleWithWebIdentityResponse xmlns=\"https://sts.amazonaws.com/doc/2011-06-15/\">
  <AssumeRoleWithWebIdentityResult>
    <Credentials>
      <AccessKeyId>ASIAEXAMPLE</AccessKeyId>
      <SecretAccessKey>secret/example</SecretAccessKey>
      <SessionToken>token/example</SessionToken>
    </Credentials>
  </AssumeRoleWithWebIdentityResult>
</AssumeRoleWithWebIdentityResponse>"""


class _FakeConn:
    def __init__(self):
        self.sql = []

    def execute(self, sql):
        self.sql.append(sql)


_SAMPLE_REGISTRY = {
    "kpi_monthly_summary": {
        "description": "Monthly KPI summary",
        "tier": "kpi",
        "columns": [{"name": "trip_count", "type": "int64"}],
        "example_questions": [],
    }
}


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


def test_vegalite_spec_uses_responsive_chart_dimensions():
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]
    spec = chart_spec_to_vegalite(
        {"type": "line", "x": "pickup_month", "y": "total_revenue"},
        rows,
    )

    assert spec["height"] >= 420
    assert spec["autosize"] == {"type": "fit", "contains": "padding", "resize": True}


def test_html_artifact_does_not_clip_chart_when_iframe_resize_is_blocked():
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]
    html = build_html_artifact(
        {"type": "line", "x": "pickup_month", "y": "total_revenue"},
        rows,
    )

    assert "overflow: hidden" not in html
    assert "min-height: 420px" in html
    assert "height: 420px" in html
    assert "renderer: 'canvas'" in html


def test_create_s3_secret_uses_web_identity_when_irsa_env_present():
    from filter_analytics import _create_s3_secret

    conn = _FakeConn()
    env = {
        "AWS_ROLE_ARN": "arn:aws:iam::492372116094:role/llmops-cluster-analytics-open-webui",
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/secrets/eks.amazonaws.com/serviceaccount/token",
    }

    with patch.dict("os.environ", env, clear=True), \
         patch("builtins.open", mock_open(read_data="jwt-token")), \
         patch("urllib.request.urlopen", return_value=_FakeHTTPResponse()):
        auth_mode = _create_s3_secret(conn, "ap-southeast-1")

    assert auth_mode == "web_identity"
    sql = conn.sql[0]
    assert "PROVIDER CONFIG" in sql
    assert "KEY_ID 'ASIAEXAMPLE'" in sql
    assert "SESSION_TOKEN 'token/example'" in sql


def test_create_s3_secret_uses_credential_chain_without_irsa_env():
    from filter_analytics import _create_s3_secret

    conn = _FakeConn()

    with patch.dict("os.environ", {}, clear=True):
        auth_mode = _create_s3_secret(conn, "ap-southeast-1")

    assert auth_mode == "credential_chain"
    assert "PROVIDER CREDENTIAL_CHAIN" in conn.sql[0]
    assert "REGION 'ap-southeast-1'" in conn.sql[0]


def test_persist_html_artifact_creates_openwebui_html_file_marker(tmp_path):
    from filter_analytics import _persist_html_artifact

    db_path = tmp_path / "webui.db"
    upload_dir = tmp_path / "uploads"
    html = '<!DOCTYPE html><html><body><div id="chart"></div></body></html>'

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE user (id TEXT, role TEXT, created_at INTEGER)")
    conn.execute("""
        CREATE TABLE file (
            id TEXT,
            user_id TEXT,
            filename TEXT,
            meta JSON,
            created_at INTEGER,
            hash TEXT,
            data JSON,
            updated_at BIGINT,
            path TEXT
        )
    """)
    conn.execute("INSERT INTO user (id, role, created_at) VALUES ('admin-user', 'admin', 1)")
    conn.commit()
    conn.close()

    marker = _persist_html_artifact(html, db_path=str(db_path), upload_dir=str(upload_dir))

    assert marker.startswith('<file type="html" id="')
    assert marker.endswith('">')
    assert '</file>' not in marker
    file_id = marker.split('id="', 1)[1].split('"', 1)[0]

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT user_id, filename, path, meta FROM file WHERE id = ?",
        (file_id,),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "admin-user"
    assert row[1].endswith(".html")
    assert Path(row[2]).read_text(encoding="utf-8") == html
    assert upload_dir in Path(row[2]).parents



@pytest.mark.asyncio
async def test_pipe_chat_returns_streaming_response():
    from filter_analytics import Pipe
    from starlette.responses import StreamingResponse

    pipe = Pipe()
    body = {"messages": [{"role": "user", "content": "explain linked lists"}]}

    async def fake_stream(messages, litellm_url, model, api_key=""):
        async def gen():
            yield b"data: {}"
        return StreamingResponse(gen(), media_type="text/event-stream")

    with patch("filter_analytics._stream_llm", side_effect=fake_stream):
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

    async def fake_stream(*args, **kwargs):
        yield "> **Table:** `kpi`\n"
        yield "Summary"

    with patch("filter_analytics._stream_analytics", return_value=fake_stream()):
        result = await pipe.pipe(body, __event_emitter__=mock_emitter)

    assert result == ""
    message_events = [e for e in emitted if e["type"] == "message"]
    assert len(message_events) == 2
    assert message_events[0]["data"]["content"] == "> **Table:** `kpi`\n"
    assert message_events[1]["data"]["content"] == "Summary"


@pytest.mark.asyncio
async def test_pipe_analytics_skips_emitter_when_none():
    from filter_analytics import Pipe

    pipe = Pipe()
    body = {"messages": [{"role": "user", "content": "show monthly revenue trend for taxi trips"}]}

    async def fake_stream(*args, **kwargs):
        yield "Response"

    with patch("filter_analytics._stream_analytics", return_value=fake_stream()):
        result = await pipe.pipe(body, __event_emitter__=None)

    assert result == "Response"


@pytest.mark.asyncio
async def test_pipe_ambiguous_returns_clarification():
    from filter_analytics import Pipe

    pipe = Pipe()
    # "taxi" is a domain term but no analytics keyword → INTENT_AMBIGUOUS
    body = {"messages": [{"role": "user", "content": "taxi"}]}

    result = await pipe.pipe(body)

    assert isinstance(result, str)
    assert "analytics" in result.lower()


# ---------------------------------------------------------------------------
# _load_registry TTL cache tests
# ---------------------------------------------------------------------------

_SAMPLE_REGISTRY = {
    "kpi_monthly_summary": {
        "description": "Monthly KPI summary",
        "tier": "kpi",
        "columns": [{"name": "trip_count", "type": "int64"}],
        "example_questions": [],
    }
}


def _reset_registry_cache():
    """Reset the module-level cache between tests."""
    import filter_analytics
    filter_analytics._registry_cache = None
    filter_analytics._registry_ts = 0.0


def test_load_registry_cache_hit_returns_cached_without_fetch():
    """When TTL has not expired, return the cached registry without calling fetch."""
    import filter_analytics
    _reset_registry_cache()

    # Prime the cache manually
    filter_analytics._registry_cache = _SAMPLE_REGISTRY
    filter_analytics._registry_ts = time.time()  # just fetched

    with patch("filter_analytics._fetch_registry_from_s3") as mock_fetch:
        result = _load_registry("my-bucket", "ap-southeast-1", ttl=300)

    mock_fetch.assert_not_called()
    assert result == _SAMPLE_REGISTRY


def test_load_registry_cache_miss_fetches_from_s3():
    """When TTL has expired (or no cache), fetch from S3 and update the cache."""
    import filter_analytics
    _reset_registry_cache()

    with patch("filter_analytics._fetch_registry_from_s3", return_value=_SAMPLE_REGISTRY) as mock_fetch:
        result = _load_registry("my-bucket", "ap-southeast-1", ttl=300)

    mock_fetch.assert_called_once_with("my-bucket", "ap-southeast-1")
    assert result == _SAMPLE_REGISTRY
    assert filter_analytics._registry_cache == _SAMPLE_REGISTRY
    assert filter_analytics._registry_ts > 0


def test_load_registry_stale_cache_fallback_on_s3_error():
    """When S3 fetch fails but we have a stale cache, return stale data instead of raising."""
    import filter_analytics
    _reset_registry_cache()

    stale = {"old_table": {"description": "old", "tier": "kpi", "columns": [], "example_questions": []}}
    filter_analytics._registry_cache = stale
    filter_analytics._registry_ts = 0.0  # expired TTL

    with patch("filter_analytics._fetch_registry_from_s3", side_effect=RuntimeError("S3 unavailable")):
        result = _load_registry("my-bucket", "ap-southeast-1", ttl=300)

    assert result == stale


def test_load_registry_first_call_failure_raises():
    """When there's no cache and S3 fetch fails, propagate the exception."""
    import filter_analytics
    _reset_registry_cache()

    with patch("filter_analytics._fetch_registry_from_s3", side_effect=RuntimeError("S3 unavailable")):
        with pytest.raises(RuntimeError, match="S3 unavailable"):
            _load_registry("my-bucket", "ap-southeast-1", ttl=300)


# ---------------------------------------------------------------------------
# _run_chart_spec tests
# ---------------------------------------------------------------------------

def test_run_chart_spec_returns_valid_spec():
    from filter_analytics import _run_chart_spec
    rows = [{"pickup_month": 1, "total_revenue": 10.0}, {"pickup_month": 2, "total_revenue": 20.0}]
    llm_response = '{"chart_spec": {"type": "line", "x": "pickup_month", "y": "total_revenue"}}'
    with patch("filter_analytics._llm_chat", return_value=llm_response):
        result = _run_chart_spec("show revenue trend", rows)
    assert result == {"type": "line", "x": "pickup_month", "y": "total_revenue"}


def test_run_chart_spec_returns_none_on_invalid_columns():
    from filter_analytics import _run_chart_spec
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]
    llm_response = '{"chart_spec": {"type": "bar", "x": "nonexistent", "y": "total_revenue"}}'
    with patch("filter_analytics._llm_chat", return_value=llm_response):
        result = _run_chart_spec("show revenue", rows)
    assert result is None


def test_run_chart_spec_returns_none_on_llm_error():
    from filter_analytics import _run_chart_spec
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]
    with patch("filter_analytics._llm_chat", side_effect=Exception("LLM timeout")):
        result = _run_chart_spec("show revenue", rows)
    assert result is None


# ---------------------------------------------------------------------------
# _stream_summary tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_summary_yields_tokens():
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]

    sse_lines = (
        b'data: {"choices":[{"delta":{"content":"Revenue "}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"grew."}}]}\n\n'
        b'data: [DONE]\n\n'
    )

    class FakeResponse:
        async def aiter_bytes(self):
            yield sse_lines
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    class FakeClient:
        def stream(self, *args, **kwargs):
            return FakeResponse()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        tokens = []
        async for token in _stream_summary("show revenue", rows, capped=False):
            tokens.append(token)

    assert "".join(tokens) == "Revenue grew."


# ---------------------------------------------------------------------------
# _stream_analytics tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_analytics_yields_reasoning_trace_and_summary():
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [{"name": "pickup_month", "type": "int32"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Monthly summary"}}

    emitted = []
    async def mock_emitter(event):
        emitted.append(event)

    async def fake_summary(*args, **kwargs):
        yield "Revenue "
        yield "grew."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Monthly revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT pickup_month, total_revenue FROM kpi_monthly_summary", "rows": rows, "capped": False}), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "line", "x": "pickup_month", "y": "total_revenue"}), \
         patch("filter_analytics.build_html_artifact", return_value="<html>chart</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show revenue trend", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    full_response = "".join(chunks)
    assert "> **Table:**" in full_response
    assert "kpi_monthly_summary" in full_response
    assert "> **SQL:**" in full_response
    assert "> **Result:**" in full_response
    assert "---" in full_response
    assert "Revenue grew." in full_response
    status_events = [e for e in emitted if e["type"] == "status"]
    assert len(status_events) >= 4


@pytest.mark.asyncio
async def test_stream_analytics_yields_clarification_on_low_confidence():
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [], "example_questions": [], "description": "Monthly summary"}}

    emitted = []
    async def mock_emitter(event):
        emitted.append(event)

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "low", "reasoning": "Unclear question"}):

        chunks = []
        async for chunk in _stream_analytics("something vague", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    full_response = "".join(chunks)
    assert "confidence: low" in full_response
    assert "more specific" in full_response.lower()


@pytest.mark.asyncio
async def test_stream_analytics_yields_error_on_query_failure():
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [], "example_questions": [], "description": "Monthly summary"}}

    emitted = []
    async def mock_emitter(event):
        emitted.append(event)

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Good match"}), \
         patch("filter_analytics._run_query", side_effect=TimeoutError("DuckDB query exceeded 30s")):

        chunks = []
        async for chunk in _stream_analytics("show revenue", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    full_response = "".join(chunks)
    assert "> **Error:**" in full_response
    assert "30s" in full_response


# ---------------------------------------------------------------------------
# Full integration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pipe_integration_streams_trace_and_summary():
    from filter_analytics import Pipe

    pipe = Pipe()
    body = {"messages": [{"role": "user", "content": "show monthly revenue for taxi trips"}]}

    emitted = []
    async def mock_emitter(event):
        emitted.append(event)

    rows = [
        {"pickup_month": 1, "total_revenue": 10.0},
        {"pickup_month": 2, "total_revenue": 20.0},
    ]
    registry = {
        "kpi_monthly_summary": {
            "tier": "kpi",
            "columns": [{"name": "pickup_month", "type": "int32"}, {"name": "total_revenue", "type": "double"}],
            "example_questions": ["show monthly revenue"],
            "description": "Monthly aggregated revenue",
        }
    }

    async def fake_stream_summary(*args, **kwargs):
        yield "Revenue grew "
        yield "from $10M to $20M."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Monthly revenue match"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT pickup_month, total_revenue FROM kpi_monthly_summary", "rows": rows, "capped": False}), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "line", "x": "pickup_month", "y": "total_revenue"}), \
         patch("filter_analytics.build_html_artifact", return_value="<html>chart</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_stream_summary()):

        result = await pipe.pipe(body, __event_emitter__=mock_emitter)

    assert result == ""

    message_events = [e for e in emitted if e["type"] == "message"]
    full = "".join(e["data"]["content"] for e in message_events)

    assert "> **Table:** `kpi_monthly_summary`" in full
    assert "Monthly revenue match" in full
    assert "> **SQL:**" in full
    assert "> **Result:** 2 rows" in full
    assert "---" in full
    assert "Revenue grew from $10M to $20M." in full

    embed_events = [e for e in emitted if e["type"] == "embeds"]
    assert len(embed_events) == 1
    assert "<html>chart</html>" in embed_events[0]["data"]["embeds"]

    status_events = [e for e in emitted if e["type"] == "status"]
    assert any("Selecting" in e["data"]["description"] for e in status_events)
    assert any("Done" in e["data"]["description"] for e in status_events)

    status_events = [e for e in emitted if e["type"] == "status"]
    assert any("Selecting" in e["data"]["description"] for e in status_events)
    assert any("Done" in e["data"]["description"] for e in status_events)
