import pytest
import sqlite3
import sys
import time
import json
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import (
    _strip_fences,
    _split_plan_and_sql,
    _wrap_with_limit,
    _retry_prompt,
    _validate_sql,
    SQLValidationError,
    build_html_artifact,
    chart_spec_to_vegalite,
    _load_registry,
    _stream_summary,
    _stream_analytics,
)


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


def test_select_table_candidates_exact_normalized_table_name():
    from filter_analytics import _select_table_candidates

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": [],
            "example_questions": [],
        },
        "kpi_daily_overview": {
            "description": "Daily revenue and trips",
            "tier": "kpi",
            "columns": [{"name": "pickup_date", "type": "date32[day]"}],
            "aliases": [],
            "example_questions": [],
        },
    }

    candidates = _select_table_candidates("Show me table kpi zone net flow", registry)

    assert candidates[0]["table"] == "kpi_zone_net_flow"
    assert candidates[0]["match_type"] == "exact_table_name"
    assert candidates[0]["score"] >= 1000
    assert "normalized table name matched" in candidates[0]["reasons"]


def test_select_table_candidates_exact_alias_match():
    from filter_analytics import _select_table_candidates

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": ["zone inflow outflow"],
            "example_questions": [],
        }
    }

    candidates = _select_table_candidates("show me zone inflow outflow", registry)

    assert candidates[0]["table"] == "kpi_zone_net_flow"
    assert candidates[0]["match_type"] == "exact_alias"
    assert "alias matched: zone inflow outflow" in candidates[0]["reasons"]


def test_select_table_candidates_scores_metadata_and_columns():
    from filter_analytics import _select_table_candidates

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}, {"name": "borough", "type": "string"}],
            "aliases": ["zone net flow"],
            "measures": ["net_flow", "imbalance_score"],
            "dimensions": ["zone", "borough"],
            "use_for": ["zone pickup dropoff imbalance"],
            "example_questions": [],
        },
        "kpi_monthly_summary": {
            "description": "Monthly revenue trend",
            "tier": "kpi",
            "columns": [{"name": "pickup_month", "type": "int32"}],
            "aliases": [],
            "measures": ["total_revenue"],
            "dimensions": ["pickup_month"],
            "use_for": ["monthly trends"],
            "example_questions": [],
        },
    }

    candidates = _select_table_candidates("which zone has the largest pickup dropoff imbalance", registry)

    assert candidates[0]["table"] == "kpi_zone_net_flow"
    assert candidates[0]["score"] > candidates[1]["score"]


def test_select_table_candidates_returns_empty_for_no_signal():
    from filter_analytics import _select_table_candidates

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": [],
            "example_questions": [],
        }
    }

    assert _select_table_candidates("explain linked lists", registry) == []


def test_registry_as_prompt_includes_optional_metadata():
    from filter_analytics import _registry_as_prompt

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": ["kpi zone net flow", "zone net flow"],
            "grain": "one row per taxi zone",
            "dimensions": ["zone", "borough"],
            "measures": ["net_flow", "imbalance_score"],
            "date_columns": [],
            "use_for": ["zone pickup/dropoff imbalance"],
            "avoid_for": ["daily trends because this table has no date column"],
            "example_questions": ["show table kpi zone net flow"],
        }
    }

    prompt = _registry_as_prompt(registry)

    assert "aliases: kpi zone net flow; zone net flow" in prompt
    assert "grain: one row per taxi zone" in prompt
    assert "dimensions: zone, borough" in prompt
    assert "measures: net_flow, imbalance_score" in prompt
    assert "date_columns: none" in prompt
    assert "use_for: zone pickup/dropoff imbalance" in prompt
    assert "avoid_for: daily trends because this table has no date column" in prompt
    assert "examples: show table kpi zone net flow" in prompt


def test_registry_as_prompt_supports_old_minimal_entries():
    from filter_analytics import _registry_as_prompt

    registry = {
        "kpi_monthly_summary": {
            "description": "Monthly summary",
            "tier": "kpi",
            "columns": [{"name": "total_revenue", "type": "double"}],
            "example_questions": [],
        }
    }

    prompt = _registry_as_prompt(registry)

    assert "kpi_monthly_summary" in prompt
    assert "total_revenue(double)" in prompt
    assert "aliases:" not in prompt
    assert "date_columns:" not in prompt


def test_run_supervisor_can_receive_candidate_registry_only():
    from filter_analytics import _run_supervisor

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": ["zone net flow"],
            "example_questions": [],
        }
    }

    captured_messages = {}

    def fake_llm(messages, model, litellm_url, api_key=""):
        captured_messages["user"] = messages[1]["content"]
        return '{"table": "kpi_zone_net_flow", "confidence": "high", "reasoning": "best candidate"}'

    with patch("filter_analytics._llm_chat", side_effect=fake_llm):
        result = _run_supervisor(
            "which zone has the largest net flow",
            registry,
            "http://litellm",
            "private-chat",
            "",
        )

    assert result["table"] == "kpi_zone_net_flow"
    assert "kpi_zone_net_flow" in captured_messages["user"]
    assert "Available tables:" in captured_messages["user"]


def test_run_supervisor_unknown_table_returns_low_confidence():
    from filter_analytics import _run_supervisor

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "example_questions": [],
        }
    }

    def fake_llm(messages, model, litellm_url, api_key=""):
        return '{"table": "fact", "confidence": "high", "reasoning": "raw records"}'

    with patch("filter_analytics._llm_chat", side_effect=fake_llm):
        result = _run_supervisor(
            "Show exact individual taxi trip records with passenger names and payment card numbers.",
            registry,
            "http://litellm",
            "private-chat",
            "",
        )

    assert result["confidence"] == "low"
    assert result["table"] == ""
    assert "not listed" in result["reasoning"]


def test_strip_fences_removes_sql_block():
    assert _strip_fences("```sql\nSELECT 1\n```") == "SELECT 1"


def test_strip_fences_removes_plain_block():
    assert _strip_fences("```\nSELECT 1\n```") == "SELECT 1"


def test_strip_fences_passthrough_plain_sql():
    assert _strip_fences("SELECT 1") == "SELECT 1"



def test_split_plan_and_sql_extracts_first_anchored_sql_block():
    plan, sql = _split_plan_and_sql(
        "PLAN: Use route_top_pickup_zones at zone grain.\n"
        "SQL:\n"
        "SELECT pickup_zone, revenue FROM route_top_pickup_zones"
    )

    assert plan == "Use route_top_pickup_zones at zone grain."
    assert sql == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"


def test_split_plan_and_sql_missing_delimiter_returns_empty_plan():
    text = "SELECT pickup_zone, revenue FROM route_top_pickup_zones"

    plan, sql = _split_plan_and_sql(text)

    assert plan == ""
    assert sql == text


def test_split_plan_and_sql_ignores_mid_sentence_sql_colon():
    plan, sql = _split_plan_and_sql(
        "PLAN: I will write SQL: a single SELECT at zone grain.\n"
        "SQL:\n"
        "SELECT pickup_zone, revenue FROM route_top_pickup_zones"
    )

    assert plan == "I will write SQL: a single SELECT at zone grain."
    assert sql == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"


def test_split_plan_and_sql_is_case_insensitive():
    plan, sql = _split_plan_and_sql(
        "plan: The table is already at pickup-zone grain.\n"
        "sql:\n"
        "SELECT pickup_zone, revenue FROM route_top_pickup_zones"
    )

    assert plan == "The table is already at pickup-zone grain."
    assert sql == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"


def test_split_plan_and_sql_preserves_multiline_plan():
    plan, sql = _split_plan_and_sql(
        "PLAN: route_top_pickup_zones is pre-aggregated.\n"
        "The answer should stay at pickup-zone grain and retain borough context.\n"
        "SQL:\n"
        "SELECT pickup_zone, pickup_borough, revenue FROM route_top_pickup_zones"
    )

    assert plan == (
        "route_top_pickup_zones is pre-aggregated.\n"
        "The answer should stay at pickup-zone grain and retain borough context."
    )
    assert sql == "SELECT pickup_zone, pickup_borough, revenue FROM route_top_pickup_zones"


def test_wrap_with_limit_wraps_query_without_top_level_limit():
    sql = "SELECT pickup_zone, revenue FROM route_top_pickup_zones ORDER BY revenue DESC"

    wrapped, applied_cap = _wrap_with_limit(sql, row_cap=20)

    assert wrapped == (
        "SELECT * FROM (SELECT pickup_zone, revenue FROM route_top_pickup_zones "
        "ORDER BY revenue DESC) _q LIMIT 21"
    )
    assert applied_cap is True


def test_wrap_with_limit_preserves_existing_top_level_limit():
    sql = "SELECT pickup_zone, revenue FROM route_top_pickup_zones LIMIT 20"

    wrapped, applied_cap = _wrap_with_limit(sql, row_cap=20)

    assert wrapped == sql
    assert applied_cap is False


def test_wrap_with_limit_still_wraps_when_limit_is_inside_cte():
    sql = (
        "WITH ranked AS ("
        "SELECT pickup_zone, revenue FROM route_top_pickup_zones LIMIT 500"
        ") SELECT pickup_zone, revenue FROM ranked"
    )

    wrapped, applied_cap = _wrap_with_limit(sql, row_cap=20)

    assert wrapped == f"SELECT * FROM ({sql}) _q LIMIT 21"
    assert applied_cap is True


def test_retry_prompt_includes_error_table_and_output_contract():
    prompt = _retry_prompt(
        SQLValidationError("Table 'bad' not allowed"),
        "route_top_pickup_zones",
    )

    assert "Your SQL was rejected: Table 'bad' not allowed." in prompt
    assert "GROUP BY rules" in prompt
    assert "columns list" in prompt
    assert "ONE SELECT against route_top_pickup_zones" in prompt
    assert "Return PLAN then SQL" in prompt


def _query_registry():
    return {
        "route_top_pickup_zones": {
            "description": "Top pickup zones",
            "tier": "route",
            "columns": [
                {"name": "pickup_zone", "type": "string"},
                {"name": "pickup_borough", "type": "string"},
                {"name": "revenue", "type": "double"},
            ],
            "example_questions": [],
        }
    }


def test_execute_sql_fetches_dict_rows():
    from filter_analytics import _execute_sql

    class FakeFrame:
        def to_dict(self, orient):
            assert orient == "records"
            return [{"pickup_zone": "Midtown", "revenue": 12.5}]

    class FakeExecuted:
        def fetchdf(self):
            return FakeFrame()

    class FakeConn:
        def __init__(self):
            self.sql = None

        def execute(self, sql):
            self.sql = sql
            return FakeExecuted()

    conn = FakeConn()

    rows = _execute_sql(conn, "SELECT pickup_zone, revenue FROM route_top_pickup_zones")

    assert rows == [{"pickup_zone": "Midtown", "revenue": 12.5}]
    assert conn.sql == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"


def test_build_duckdb_conn_installs_httpfs_creates_secret_and_view():
    from filter_analytics import _build_duckdb_conn

    fake_conn = MagicMock()

    with patch("filter_analytics.duckdb.connect", return_value=fake_conn) as mock_connect, \
         patch("filter_analytics._create_s3_secret", return_value="web_identity") as mock_secret:
        conn = _build_duckdb_conn("route_top_pickup_zones", "analytics-bucket", "ap-southeast-1")

    assert conn is fake_conn
    mock_connect.assert_called_once_with(
        config={
            "memory_limit": "512MB",
            "extension_directory": "/tmp/duckdb-extensions",
        }
    )
    mock_secret.assert_called_once_with(fake_conn, "ap-southeast-1")
    executed_sql = [call.args[0] for call in fake_conn.execute.call_args_list]
    assert executed_sql[0] == "INSTALL httpfs; LOAD httpfs;"
    assert executed_sql[1] == "CREATE VIEW route_top_pickup_zones AS SELECT * FROM read_parquet('s3://analytics-bucket/route_top_pickup_zones/*.parquet')"


def test_run_query_retries_on_duckdb_binder_error():
    import duckdb
    from filter_analytics import _run_query

    first_sql = (
        "PLAN: Wrongly aggregate by borough.\n"
        "SQL:\n"
        "SELECT pickup_borough, revenue FROM route_top_pickup_zones GROUP BY pickup_borough"
    )
    second_sql = (
        "PLAN: Keep zone grain and retain borough context.\n"
        "SQL:\n"
        "SELECT pickup_zone, pickup_borough, revenue FROM route_top_pickup_zones ORDER BY revenue DESC LIMIT 20"
    )
    rows = [{"pickup_zone": "Midtown", "pickup_borough": "Manhattan", "revenue": 100.0}]

    with patch("filter_analytics._llm_chat", side_effect=[first_sql, second_sql]) as mock_llm, \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", side_effect=[duckdb.BinderException('column "revenue" must appear in the GROUP BY clause'), rows]):
        mock_build.return_value = MagicMock()
        result = _run_query(
            "List the top 20 pickup zones by total taxi revenue following pickup borough",
            "route_top_pickup_zones",
            _query_registry(),
            "analytics-bucket",
            "ap-southeast-1",
        )

    assert mock_llm.call_count == 2
    assert result["plan"] == "Keep zone grain and retain borough context."
    assert result["sql"] == "SELECT pickup_zone, pickup_borough, revenue FROM route_top_pickup_zones ORDER BY revenue DESC LIMIT 20"
    assert result["rows"] == rows
    retry_messages = mock_llm.call_args_list[1].args[0]
    assert 'column "revenue" must appear in the GROUP BY clause' in retry_messages[-1]["content"]


def test_run_query_retries_on_catalog_error():
    import duckdb
    from filter_analytics import _run_query

    rows = [{"pickup_zone": "Midtown", "revenue": 100.0}]

    with patch("filter_analytics._llm_chat", side_effect=[
        "PLAN: Use a missing column.\nSQL:\nSELECT missing_column FROM route_top_pickup_zones",
        "PLAN: Use known columns.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones LIMIT 20",
    ]) as mock_llm, \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", side_effect=[duckdb.CatalogException("Column missing_column not found"), rows]):
        mock_build.return_value = MagicMock()
        result = _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    assert mock_llm.call_count == 2
    assert result["rows"] == rows
    assert result["plan"] == "Use known columns."


def test_run_query_raises_after_two_duckdb_failures():
    import duckdb
    from filter_analytics import _run_query

    with patch("filter_analytics._llm_chat", side_effect=[
        "PLAN: First attempt.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones",
        "PLAN: Second attempt.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones",
    ]) as mock_llm, \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", side_effect=[
             duckdb.BinderException("first binder failure"),
             duckdb.BinderException("second binder failure"),
         ]):
        mock_build.return_value = MagicMock()
        with pytest.raises(duckdb.Error, match="second binder failure"):
            _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    assert mock_llm.call_count == 2


def test_run_query_validator_then_duckdb_error_in_one_session():
    import duckdb
    from filter_analytics import _run_query

    with patch("filter_analytics._llm_chat", side_effect=[
        "PLAN: Try file access.\nSQL:\nSELECT * FROM read_parquet('s3://x')",
        "PLAN: Use the table.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones",
    ]) as mock_llm, \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", side_effect=duckdb.BinderException("binder after validation")) as mock_execute:
        mock_build.return_value = MagicMock()
        with pytest.raises(duckdb.Error, match="binder after validation"):
            _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    assert mock_llm.call_count == 2
    assert mock_execute.call_count == 1


def test_run_query_extracts_plan_and_sql():
    from filter_analytics import _run_query

    rows = [{"pickup_zone": "Midtown", "revenue": 100.0}]
    with patch("filter_analytics._llm_chat", return_value="PLAN: Use zone grain.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones"), \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", return_value=rows):
        mock_build.return_value = MagicMock()
        result = _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    assert result["plan"] == "Use zone grain."
    assert result["sql"] == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"
    assert result["rows"] == rows


def test_run_query_handles_missing_plan_delimiter():
    from filter_analytics import _run_query

    rows = [{"pickup_zone": "Midtown", "revenue": 100.0}]
    with patch("filter_analytics._llm_chat", return_value="SELECT pickup_zone, revenue FROM route_top_pickup_zones"), \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", return_value=rows):
        mock_build.return_value = MagicMock()
        result = _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    assert result["plan"] == ""
    assert result["sql"] == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"


def test_run_query_strips_fences_around_full_plan_and_sql():
    from filter_analytics import _run_query

    rows = [{"pickup_zone": "Midtown", "revenue": 100.0}]
    raw = "```sql\nPLAN: Use zone grain.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones\n```"
    with patch("filter_analytics._llm_chat", return_value=raw), \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", return_value=rows):
        mock_build.return_value = MagicMock()
        result = _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    assert result["plan"] == "Use zone grain."
    assert result["sql"] == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"


def test_run_query_ignores_sql_colon_inside_plan_text():
    from filter_analytics import _run_query

    rows = [{"pickup_zone": "Midtown", "revenue": 100.0}]
    raw = (
        "PLAN: I will write SQL: a SELECT that keeps pickup-zone grain.\n"
        "SQL:\n"
        "SELECT pickup_zone, revenue FROM route_top_pickup_zones"
    )
    with patch("filter_analytics._llm_chat", return_value=raw), \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", return_value=rows):
        mock_build.return_value = MagicMock()
        result = _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    assert result["plan"] == "I will write SQL: a SELECT that keeps pickup-zone grain."
    assert result["sql"] == "SELECT pickup_zone, revenue FROM route_top_pickup_zones"


def test_run_query_retry_message_includes_error_verbatim():
    import duckdb
    from filter_analytics import _run_query

    with patch("filter_analytics._llm_chat", side_effect=[
        "PLAN: Bad group by.\nSQL:\nSELECT pickup_borough, revenue FROM route_top_pickup_zones GROUP BY pickup_borough",
        "PLAN: Corrected.\nSQL:\nSELECT pickup_zone, pickup_borough, revenue FROM route_top_pickup_zones LIMIT 20",
    ]) as mock_llm, \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", side_effect=[
             duckdb.BinderException('Binder Error: column "revenue" must appear in the GROUP BY clause'),
             [{"pickup_zone": "Midtown", "pickup_borough": "Manhattan", "revenue": 100.0}],
         ]):
        mock_build.return_value = MagicMock()
        _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    second_messages = mock_llm.call_args_list[1].args[0]
    assert second_messages[-2]["role"] == "assistant"
    assert second_messages[-2]["content"].startswith("PLAN: Bad group by.")
    assert second_messages[-1]["role"] == "user"
    assert 'Binder Error: column "revenue" must appear in the GROUP BY clause' in second_messages[-1]["content"]


def test_run_query_reuses_connection_across_attempts():
    import duckdb
    from filter_analytics import _run_query

    fake_conn = MagicMock()
    with patch("filter_analytics._llm_chat", side_effect=[
        "PLAN: First.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones",
        "PLAN: Second.\nSQL:\nSELECT pickup_zone, revenue FROM route_top_pickup_zones LIMIT 20",
    ]), \
         patch("filter_analytics.duckdb.connect", return_value=fake_conn) as mock_connect, \
         patch("filter_analytics._create_s3_secret", return_value="web_identity"), \
         patch("filter_analytics._execute_sql", side_effect=[
             duckdb.BinderException("first failure"),
             [{"pickup_zone": "Midtown", "revenue": 100.0}],
         ]):
        _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    mock_connect.assert_called_once()
    fake_conn.close.assert_called_once()


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
        gen = await pipe.pipe(body, __event_emitter__=mock_emitter)
        chunks = [chunk async for chunk in gen]

    assert "".join(chunks) == "> **Table:** `kpi`\nSummary"


@pytest.mark.asyncio
async def test_pipe_analytics_skips_emitter_when_none():
    from filter_analytics import Pipe

    pipe = Pipe()
    body = {"messages": [{"role": "user", "content": "show monthly revenue trend for taxi trips"}]}

    async def fake_stream(*args, **kwargs):
        yield "Response"

    with patch("filter_analytics._stream_analytics", return_value=fake_stream()):
        gen = await pipe.pipe(body, __event_emitter__=None)
        chunks = [chunk async for chunk in gen]

    assert "".join(chunks) == "Response"


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
        status_code = 200
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
async def test_stream_analytics_exact_alias_does_not_return_low_confidence_message():
    from filter_analytics import _stream_analytics

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": ["kpi zone net flow"],
            "example_questions": [],
        }
    }

    async def fake_summary(*args, **kwargs):
        yield "The table has one matching row."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_query", return_value={
             "sql": "SELECT net_flow FROM kpi_zone_net_flow",
             "rows": [{"net_flow": 5}],
             "capped": False,
         }), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "table", "x": "net_flow", "y": "net_flow"}), \
         patch("filter_analytics._persist_html_artifact", return_value='<file type="html" id="table-id">'), \
         patch("filter_analytics._stream_summary", side_effect=fake_summary):
        chunks = []
        async for chunk in _stream_analytics(
            "Show me table kpi zone net flow",
            "bucket",
            "ap-southeast-1",
            "http://litellm",
            "private-chat",
            "",
            300,
            30,
            200,
            None,
        ):
            chunks.append(chunk)

    response = "".join(chunks)
    assert "confidence: high" in response
    assert "I wasn't confident" not in response
    assert "SELECT net_flow FROM kpi_zone_net_flow" in response


@pytest.mark.asyncio
async def test_stream_analytics_multiple_exact_matches_uses_candidate_supervisor():
    from filter_analytics import _stream_analytics

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": [],
            "example_questions": [],
        },
        "kpi_zone_performance": {
            "description": "Zone-level performance metrics",
            "tier": "kpi",
            "columns": [{"name": "pickups", "type": "int64"}],
            "aliases": [],
            "example_questions": [],
        },
        "kpi_monthly_summary": {
            "description": "Monthly revenue trend",
            "tier": "kpi",
            "columns": [{"name": "total_revenue", "type": "double"}],
            "aliases": [],
            "example_questions": [],
        },
    }

    async def fake_summary(*args, **kwargs):
        yield "Zone performance is available."

    captured_registry = {}

    def fake_supervisor(question, prompt_registry, litellm_url, litellm_model, api_key):
        captured_registry.update(prompt_registry)
        return {
            "table": "kpi_zone_performance",
            "confidence": "high",
            "reasoning": "multiple exact matches required supervisor choice",
        }

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", side_effect=fake_supervisor) as mock_supervisor, \
         patch("filter_analytics._run_query", return_value={
             "sql": "SELECT pickups FROM kpi_zone_performance",
             "rows": [{"pickups": 10}],
             "capped": False,
         }), \
         patch("filter_analytics._run_chart_spec", return_value=None), \
         patch("filter_analytics._stream_summary", side_effect=fake_summary):
        chunks = []
        async for chunk in _stream_analytics(
            "compare kpi zone performance and kpi zone net flow",
            "bucket",
            "ap-southeast-1",
            "http://litellm",
            "private-chat",
            "",
            300,
            30,
            200,
            None,
        ):
            chunks.append(chunk)

    mock_supervisor.assert_called_once()
    assert set(captured_registry) == {"kpi_zone_net_flow", "kpi_zone_performance"}
    assert "kpi_monthly_summary" not in captured_registry
    assert "**Table:** `kpi_zone_performance`" in "".join(chunks)


@pytest.mark.asyncio
async def test_stream_analytics_exact_table_match_skips_supervisor_llm():
    from filter_analytics import _stream_analytics

    registry = {
        "kpi_zone_net_flow": {
            "description": "Zone-level pickup/dropoff imbalance",
            "tier": "kpi",
            "columns": [{"name": "net_flow", "type": "int64"}],
            "aliases": ["kpi zone net flow"],
            "example_questions": [],
        }
    }

    query_result = {
        "sql": "SELECT net_flow FROM kpi_zone_net_flow",
        "rows": [{"net_flow": 10}],
        "capped": False,
    }

    async def fake_summary(*args, **kwargs):
        yield "Net flow is available."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._llm_chat") as mock_llm_chat, \
         patch("filter_analytics._run_query", return_value=query_result), \
         patch("filter_analytics._run_chart_spec", return_value=None), \
         patch("filter_analytics._stream_summary", side_effect=fake_summary):
        chunks = []
        async for chunk in _stream_analytics(
            "Show me table kpi zone net flow",
            "bucket",
            "ap-southeast-1",
            "http://litellm",
            "private-chat",
            "",
            300,
            30,
            200,
            None,
        ):
            chunks.append(chunk)

    response = "".join(chunks)
    mock_llm_chat.assert_not_called()
    assert "**Table:** `kpi_zone_net_flow`" in response
    assert "confidence: high" in response
    assert "normalized table name matched" in response
    assert "I wasn't confident" not in response


@pytest.mark.asyncio
async def test_stream_analytics_yields_plan_block_between_table_and_sql():
    rows = [{"pickup_zone": "Midtown", "revenue": 100.0}]
    registry = {"route_top_pickup_zones": {"tier": "route", "columns": [{"name": "pickup_zone", "type": "string"}, {"name": "revenue", "type": "double"}], "example_questions": [], "description": "Top zones"}}

    async def fake_summary(*args, **kwargs):
        yield "Midtown leads revenue."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "route_top_pickup_zones", "confidence": "high", "reasoning": "Top zones match"}), \
         patch("filter_analytics._run_query", return_value={
             "plan": "Use route_top_pickup_zones at pickup-zone grain.",
             "sql": "SELECT pickup_zone, revenue FROM route_top_pickup_zones",
             "rows": rows,
             "capped": False,
         }), \
         patch("filter_analytics._run_chart_spec", return_value=None), \
         patch("filter_analytics._stream_summary", side_effect=fake_summary):
        chunks = []
        async for chunk in _stream_analytics("show top zones", "bucket", "ap-southeast-1", "http://litellm", "private-chat", "", 300, 30, 200, None):
            chunks.append(chunk)

    response = "".join(chunks)
    assert "> **Plan:** Use route_top_pickup_zones at pickup-zone grain." in response
    assert response.index("> **Table:**") < response.index("> **Plan:**") < response.index("> **SQL:**")


@pytest.mark.asyncio
async def test_stream_analytics_omits_plan_block_when_empty_or_missing():
    rows = [{"pickup_zone": "Midtown", "revenue": 100.0}]
    registry = {"route_top_pickup_zones": {"tier": "route", "columns": [{"name": "pickup_zone", "type": "string"}, {"name": "revenue", "type": "double"}], "example_questions": [], "description": "Top zones"}}

    async def fake_summary(*args, **kwargs):
        yield "Midtown leads revenue."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "route_top_pickup_zones", "confidence": "high", "reasoning": "Top zones match"}), \
         patch("filter_analytics._run_query", return_value={
             "sql": "SELECT pickup_zone, revenue FROM route_top_pickup_zones",
             "rows": rows,
             "capped": False,
         }), \
         patch("filter_analytics._run_chart_spec", return_value=None), \
         patch("filter_analytics._stream_summary", side_effect=fake_summary):
        chunks = []
        async for chunk in _stream_analytics("show top zones", "bucket", "ap-southeast-1", "http://litellm", "private-chat", "", 300, 30, 200, None):
            chunks.append(chunk)

    assert "> **Plan:**" not in "".join(chunks)


@pytest.mark.asyncio
async def test_stream_analytics_pickup_borough_regression():
    """The reported failure: a bare `revenue` SELECT with a borough GROUP BY
    raises DuckDB BinderException on attempt 1; the corrected SELECT succeeds
    on attempt 2. The stream must not raise, must include the Plan block, and
    must surface the corrected SQL (not the buggy first attempt).
    """
    import duckdb

    registry = {
        "route_top_pickup_zones": {
            "description": "Top pickup zones",
            "tier": "route",
            "columns": [
                {"name": "pickup_zone", "type": "string"},
                {"name": "pickup_borough", "type": "string"},
                {"name": "revenue", "type": "double"},
            ],
            "example_questions": [],
            "aliases": ["top pickup zones"],
        }
    }
    rows = [
        {"pickup_zone": "Midtown", "pickup_borough": "Manhattan", "revenue": 9_300.0},
        {"pickup_zone": "JFK", "pickup_borough": "Queens", "revenue": 7_100.0},
    ]
    buggy_response = (
        "PLAN: Group by borough to feed the borough chart, drop the zone column.\n"
        "SQL:\n"
        "SELECT pickup_borough, revenue FROM route_top_pickup_zones GROUP BY pickup_borough"
    )
    fixed_response = (
        "PLAN: Pre-aggregated at zone grain — keep pickup_zone and pickup_borough "
        "so the chart agent can group downstream.\n"
        "SQL:\n"
        "SELECT pickup_zone, pickup_borough, revenue "
        "FROM route_top_pickup_zones ORDER BY revenue DESC LIMIT 20"
    )
    binder_exc = duckdb.BinderException(
        'Binder Error: column "revenue" must appear in the GROUP BY clause or '
        "must be part of an aggregate function."
    )

    async def fake_summary(*args, **kwargs):
        yield "Midtown leads with $9.3K, JFK follows at $7.1K."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={
             "table": "route_top_pickup_zones",
             "confidence": "high",
             "reasoning": "pickup zone leaderboard",
         }), \
         patch("filter_analytics._llm_chat", side_effect=[buggy_response, fixed_response]) as mock_llm, \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", side_effect=[binder_exc, rows]) as mock_execute, \
         patch("filter_analytics._run_chart_spec", return_value={
             "type": "bar", "x": "pickup_borough", "y": "revenue",
         }), \
         patch("filter_analytics.build_html_artifact", return_value="<html>chart</html>"), \
         patch("filter_analytics._stream_summary", side_effect=fake_summary):
        mock_build.return_value = MagicMock()
        chunks: list[str] = []
        async for chunk in _stream_analytics(
            "List the top 20 pickup zones by total taxi revenue, represent a chart "
            "of total revenue following pickup borough and conclude it",
            "analytics-bucket",
            "ap-southeast-1",
            "http://litellm",
            "private-chat",
            "",
            300,
            30,
            200,
            None,
        ):
            chunks.append(chunk)

    response = "".join(chunks)
    assert "> **Error:**" not in response, response
    assert mock_llm.call_count == 2
    assert mock_execute.call_count == 2
    assert "> **Plan:** Pre-aggregated at zone grain" in response
    assert "SELECT pickup_zone, pickup_borough, revenue" in response
    assert "GROUP BY pickup_borough" not in response
    assert response.index("> **Table:**") < response.index("> **Plan:**") < response.index("> **SQL:**")


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
async def test_stream_analytics_table_prompt_emits_table_artifact_after_summary():
    rows = [{"pickup_month": 1, "total_revenue": 10.0}]
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [{"name": "pickup_month", "type": "int32"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Monthly summary"}}

    emitted = []
    summary_complete = False

    async def mock_emitter(event):
        emitted.append({"event": event, "summary_complete": summary_complete})

    async def fake_summary(*args, **kwargs):
        nonlocal summary_complete
        yield "Revenue summary."
        summary_complete = True

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Monthly revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT pickup_month, total_revenue FROM kpi_monthly_summary", "rows": rows, "capped": False}), \
         patch("filter_analytics._run_chart_spec") as mock_chart_spec, \
         patch("filter_analytics.build_table_artifact", return_value="<html>table</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show monthly revenue as a table", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    assert "Revenue summary." in "".join(chunks)
    mock_chart_spec.assert_not_called()
    embed_events = [entry for entry in emitted if entry["event"]["type"] == "embeds"]
    assert len(embed_events) == 1
    assert embed_events[0]["summary_complete"] is True
    assert embed_events[0]["event"]["data"]["embeds"] == ["<html>table</html>"]


@pytest.mark.asyncio
async def test_stream_analytics_both_prompt_emits_chart_and_table_artifacts():
    rows = [{"borough": "Manhattan", "total_revenue": 10.0}]
    registry = {"kpi_borough_comparison": {"tier": "kpi", "columns": [{"name": "borough", "type": "varchar"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Borough comparison"}}

    emitted = []

    async def mock_emitter(event):
        emitted.append(event)

    async def fake_summary(*args, **kwargs):
        yield "Revenue summary."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_borough_comparison", "confidence": "high", "reasoning": "Borough revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT borough, total_revenue FROM kpi_borough_comparison", "rows": rows, "capped": True}), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "bar", "x": "borough", "y": "total_revenue"}), \
         patch("filter_analytics.build_html_artifact", return_value="<html>chart</html>"), \
         patch("filter_analytics.build_table_artifact", return_value="<html>table</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show revenue by borough with chart and table", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    assert "Revenue summary." in "".join(chunks)
    embed_events = [event for event in emitted if event["type"] == "embeds"]
    assert len(embed_events) == 1
    assert embed_events[0]["data"]["embeds"] == ["<html>chart</html>", "<html>table</html>"]


@pytest.mark.asyncio
async def test_stream_analytics_auto_table_chart_spec_emits_table_artifact():
    rows = [{"month": "Jan", "total_revenue": 10.0}]
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [{"name": "month", "type": "varchar"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Monthly summary"}}

    emitted = []

    async def mock_emitter(event):
        emitted.append(event)

    async def fake_summary(*args, **kwargs):
        yield "Revenue summary."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Monthly revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT month, total_revenue FROM kpi_monthly_summary", "rows": rows, "capped": False}), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "table", "x": "month", "y": "total_revenue"}), \
         patch("filter_analytics.build_table_artifact", return_value="<html>table</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show monthly revenue", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    assert "Revenue summary." in "".join(chunks)
    embed_events = [event for event in emitted if event["type"] == "embeds"]
    assert len(embed_events) == 1
    assert embed_events[0]["data"]["embeds"] == ["<html>table</html>"]


@pytest.mark.asyncio
async def test_stream_analytics_chart_prompt_falls_back_to_table_without_chart_spec():
    rows = [{"month": "Jan", "total_revenue": 10.0}]
    registry = {"kpi_monthly_summary": {"tier": "kpi", "columns": [{"name": "month", "type": "varchar"}, {"name": "total_revenue", "type": "double"}], "example_questions": [], "description": "Monthly summary"}}

    emitted = []

    async def mock_emitter(event):
        emitted.append(event)

    async def fake_summary(*args, **kwargs):
        yield "Revenue summary."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "Monthly revenue question"}), \
         patch("filter_analytics._run_query", return_value={"sql": "SELECT month, total_revenue FROM kpi_monthly_summary", "rows": rows, "capped": False}), \
         patch("filter_analytics._run_chart_spec", return_value=None), \
         patch("filter_analytics.build_table_artifact", return_value="<html>table</html>"), \
         patch("filter_analytics._stream_summary", return_value=fake_summary()):

        chunks = []
        async for chunk in _stream_analytics("show monthly revenue as a chart", "bucket", "ap-southeast-1", "http://litellm:4000/v1/chat/completions", "private-chat", "", 300, 30, 200, mock_emitter):
            chunks.append(chunk)

    assert "Revenue summary." in "".join(chunks)
    embed_events = [event for event in emitted if event["type"] == "embeds"]
    assert len(embed_events) == 1
    assert embed_events[0]["data"]["embeds"] == ["<html>table</html>"]


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

        gen = await pipe.pipe(body, __event_emitter__=mock_emitter)
        chunks = [chunk async for chunk in gen]
        result = "".join(chunks)

    assert "> **Table:** `kpi_monthly_summary`" in result
    assert "Monthly revenue match" in result
    assert "> **SQL:**" in result
    assert "> **Result:** 2 rows" in result
    assert "---" in result
    assert "Revenue grew from $10M to $20M." in result

    embed_events = [e for e in emitted if e["type"] == "embeds"]
    assert len(embed_events) == 1
    assert "<html>chart</html>" in embed_events[0]["data"]["embeds"]

    status_events = [e for e in emitted if e["type"] == "status"]
    assert any("Selecting" in e["data"]["description"] for e in status_events)
    assert any("Done" in e["data"]["description"] for e in status_events)

    status_events = [e for e in emitted if e["type"] == "status"]
    assert any("Selecting" in e["data"]["description"] for e in status_events)
    assert any("Done" in e["data"]["description"] for e in status_events)
