import pytest
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import _strip_fences, _validate_sql, SQLValidationError


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
    assert marker.endswith('"></file>')
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


def test_run_analytics_returns_html_file_embed_not_raw_html():
    from filter_analytics import _run_analytics

    rows = [
        {"pickup_year": 2024, "pickup_month": 1, "total_revenue": 10.0},
        {"pickup_year": 2024, "pickup_month": 2, "total_revenue": 20.0},
    ]

    with patch(
        "filter_analytics._run_supervisor",
        return_value={
            "table": "kpi_monthly_summary",
            "confidence": "high",
            "reasoning": "Monthly revenue question.",
        },
    ), patch(
        "filter_analytics._run_query",
        return_value={
            "sql": "SELECT pickup_year, pickup_month, total_revenue FROM kpi_monthly_summary",
            "rows": rows,
            "capped": False,
        },
    ), patch(
        "filter_analytics._run_summarize",
        return_value={
            "summary": "Revenue increased in February.",
            "chart_spec": {"type": "line", "x": "pickup_month", "y": "total_revenue", "series": []},
        },
    ), patch(
        "filter_analytics._persist_html_artifact",
        return_value='<file type="html" id="chart-1"></file>',
        create=True,
    ):
        result = _run_analytics("show monthly revenue trend", "bucket")

    assert "Revenue increased in February." in result
    assert '<file type="html" id="chart-1"></file>' in result
    assert "<!DOCTYPE html>" not in result
    assert "vegaEmbed" not in result


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

    with patch("filter_analytics._run_analytics", return_value="summary text"):
        result = await pipe.pipe(body, __event_emitter__=mock_emitter)

    assert result == "summary text"
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


@pytest.mark.asyncio
async def test_pipe_ambiguous_returns_clarification():
    from filter_analytics import Pipe

    pipe = Pipe()
    # "taxi" is a domain term but no analytics keyword → INTENT_AMBIGUOUS
    body = {"messages": [{"role": "user", "content": "taxi"}]}

    result = await pipe.pipe(body)

    assert isinstance(result, str)
    assert "analytics" in result.lower()
