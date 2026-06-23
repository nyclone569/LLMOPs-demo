# SQL Agent Prompt And Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the pickup-borough SQL failure by making the analytics SQL agent emit an explainable PLAN, retry once on DuckDB execution errors, and receive curated table metadata.

**Architecture:** Keep the change inside the existing Open WebUI analytics pipe. Add small pure helpers around SQL splitting, LIMIT wrapping, retry prompts, DuckDB connection setup, and execution; then refactor `_run_query` so one DuckDB connection is reused across the two-attempt LLM repair loop. Stream the new plan block additively and curate `schema_registry.json` without changing registry shape, `Pipe.pipe` routing, dependencies, or `_validate_sql`.

**Tech Stack:** Python, pytest, DuckDB, Open WebUI pipe async generator, JSON schema registry, existing `unittest.mock` patching.

---

## Context And File Map

Read before starting:

- Spec: `docs/superpowers/specs/2026-06-23-sql-agent-prompt-and-retry-design.md:1-438`
- SQL helpers and validator: `openwebui/filter_analytics.py:199-246`
- Registry prompt formatting: `openwebui/filter_analytics.py:927-950`
- S3 secret and query path: `openwebui/filter_analytics.py:1129-1303`
- Streaming analytics: `openwebui/filter_analytics.py:1429-1582`
- Existing pipeline tests and mocks: `tests/test_filter_pipeline.py:1-60` and `tests/test_filter_pipeline.py:630-1060`
- Existing intent tests: `tests/test_filter_intent.py:1-188`
- Registry template: `schema_registry.json:959-1056`
- Registry curation targets: `schema_registry.json:378-427`, `schema_registry.json:428-487`, `schema_registry.json:488-542`, `schema_registry.json:543-602`, `schema_registry.json:603-655`, `schema_registry.json:656-719`, `schema_registry.json:783-835`, `schema_registry.json:1058-1145`, `schema_registry.json:1673-1720`

Files to modify:

- Modify `openwebui/filter_analytics.py:199-246`: add `_split_plan_and_sql`.
- Modify `openwebui/filter_analytics.py:1129-1303`: replace `_QUERY_SYSTEM`, add `_retry_prompt`, `_wrap_with_limit`, `_build_duckdb_conn`, `_execute_sql`, and refactor `_run_query`.
- Modify `openwebui/filter_analytics.py:1429-1582`: emit `> **Plan:**` between table and SQL.
- Modify `tests/test_filter_pipeline.py:1-60` and append new tests near `tests/test_filter_pipeline.py:630-1060`: helper and `_run_query` tests plus streaming regression.
- Modify `tests/test_filter_intent.py:1-188`: prompt-contract assertions.
- Modify `schema_registry.json`: curate nine registry entries.
- Create `tests/test_schema_registry_curated.py`: registry metadata and prompt-surfacing tests.

Constraints:

- No new dependencies.
- No schema/API changes.
- No `Pipe.pipe` routing changes.
- `_validate_sql` behavior stays unchanged.
- Existing `_run_query` mocks that omit `plan` must keep passing through `query_result.get("plan", "")`.
- `FuturesTimeoutError` to `TimeoutError` stays outside the retry set.
- Every commit command below intentionally omits a `Co-Authored-By` trailer.

## Task 1: Split PLAN And SQL

**Files:**
- Modify: `openwebui/filter_analytics.py:199-246`
- Modify: `tests/test_filter_pipeline.py:1-60`
- Test: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Add `_split_plan_and_sql` to the import list in `tests/test_filter_pipeline.py:8`:

```python
from filter_analytics import _strip_fences, _split_plan_and_sql, _validate_sql, SQLValidationError, build_html_artifact, chart_spec_to_vegalite, _load_registry, _stream_summary, _stream_analytics
```

Append these tests after the existing `_strip_fences` tests in `tests/test_filter_pipeline.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_split_plan_and_sql_extracts_first_anchored_sql_block tests/test_filter_pipeline.py::test_split_plan_and_sql_missing_delimiter_returns_empty_plan tests/test_filter_pipeline.py::test_split_plan_and_sql_ignores_mid_sentence_sql_colon tests/test_filter_pipeline.py::test_split_plan_and_sql_is_case_insensitive tests/test_filter_pipeline.py::test_split_plan_and_sql_preserves_multiline_plan -v
```

Expected:

```text
ERROR tests/test_filter_pipeline.py - ImportError: cannot import name '_split_plan_and_sql' from 'filter_analytics'
```

- [ ] **Step 3: Implement the helper**

Add this function immediately after `_strip_fences` in `openwebui/filter_analytics.py:204`:

```python
def _split_plan_and_sql(text: str) -> tuple[str, str]:
    match = re.search(r"^SQL:\s*", text, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        return "", text.strip()

    plan = text[: match.start()].strip()
    sql = text[match.end() :].strip()
    if plan.upper().startswith("PLAN:"):
        plan = plan[5:].strip()
    return plan, sql
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_split_plan_and_sql_extracts_first_anchored_sql_block tests/test_filter_pipeline.py::test_split_plan_and_sql_missing_delimiter_returns_empty_plan tests/test_filter_pipeline.py::test_split_plan_and_sql_ignores_mid_sentence_sql_colon tests/test_filter_pipeline.py::test_split_plan_and_sql_is_case_insensitive tests/test_filter_pipeline.py::test_split_plan_and_sql_preserves_multiline_plan -v
```

Expected:

```text
5 passed
```

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "test: cover plan sql splitting"
```

## Task 2: Extract Top-Level LIMIT Wrapping

**Files:**
- Modify: `openwebui/filter_analytics.py:1194-1303`
- Modify: `tests/test_filter_pipeline.py`
- Test: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Add `_wrap_with_limit` to the import list in `tests/test_filter_pipeline.py:8`:

```python
from filter_analytics import _strip_fences, _split_plan_and_sql, _wrap_with_limit, _validate_sql, SQLValidationError, build_html_artifact, chart_spec_to_vegalite, _load_registry, _stream_summary, _stream_analytics
```

Append these tests near the helper tests in `tests/test_filter_pipeline.py`:

```python
def test_wrap_with_limit_wraps_query_without_top_level_limit():
    sql = "SELECT pickup_zone, revenue FROM route_top_pickup_zones ORDER BY revenue DESC"

    wrapped, capped = _wrap_with_limit(sql, row_cap=20)

    assert wrapped == (
        "SELECT * FROM (SELECT pickup_zone, revenue FROM route_top_pickup_zones "
        "ORDER BY revenue DESC) _q LIMIT 21"
    )
    assert capped is True


def test_wrap_with_limit_preserves_existing_top_level_limit():
    sql = "SELECT pickup_zone, revenue FROM route_top_pickup_zones LIMIT 20"

    wrapped, capped = _wrap_with_limit(sql, row_cap=20)

    assert wrapped == sql
    assert capped is False


def test_wrap_with_limit_still_wraps_when_limit_is_inside_cte():
    sql = (
        "WITH ranked AS ("
        "SELECT pickup_zone, revenue FROM route_top_pickup_zones LIMIT 500"
        ") SELECT pickup_zone, revenue FROM ranked"
    )

    wrapped, capped = _wrap_with_limit(sql, row_cap=20)

    assert wrapped == f"SELECT * FROM ({sql}) _q LIMIT 21"
    assert capped is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_wrap_with_limit_wraps_query_without_top_level_limit tests/test_filter_pipeline.py::test_wrap_with_limit_preserves_existing_top_level_limit tests/test_filter_pipeline.py::test_wrap_with_limit_still_wraps_when_limit_is_inside_cte -v
```

Expected:

```text
ERROR tests/test_filter_pipeline.py - ImportError: cannot import name '_wrap_with_limit' from 'filter_analytics'
```

- [ ] **Step 3: Implement the helper**

Add this function before `_run_query` in `openwebui/filter_analytics.py:1213`:

```python
def _wrap_with_limit(sql: str, row_cap: int = ROW_CAP) -> tuple[str, bool]:
    depth, top_limit = 0, False
    for token in re.split(r"(\(|\))", sql):
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0 and re.search(r"\bLIMIT\s+\d+", token, re.IGNORECASE):
            top_limit = True
            break

    if top_limit:
        return sql, False
    return f"SELECT * FROM ({sql}) _q LIMIT {row_cap + 1}", True
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_wrap_with_limit_wraps_query_without_top_level_limit tests/test_filter_pipeline.py::test_wrap_with_limit_preserves_existing_top_level_limit tests/test_filter_pipeline.py::test_wrap_with_limit_still_wraps_when_limit_is_inside_cte -v
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "refactor: extract analytics limit wrapping"
```

## Task 3: Add Shared Retry Prompt

**Files:**
- Modify: `openwebui/filter_analytics.py:1194-1303`
- Modify: `tests/test_filter_pipeline.py`
- Test: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add `_retry_prompt` to the import list in `tests/test_filter_pipeline.py:8`:

```python
from filter_analytics import _strip_fences, _split_plan_and_sql, _wrap_with_limit, _retry_prompt, _validate_sql, SQLValidationError, build_html_artifact, chart_spec_to_vegalite, _load_registry, _stream_summary, _stream_analytics
```

Append this test near the helper tests:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_retry_prompt_includes_error_table_and_output_contract -v
```

Expected:

```text
ERROR tests/test_filter_pipeline.py - ImportError: cannot import name '_retry_prompt' from 'filter_analytics'
```

- [ ] **Step 3: Implement the helper**

Add this function before `_wrap_with_limit` in `openwebui/filter_analytics.py:1213`:

```python
def _retry_prompt(exc: Exception, table: str) -> str:
    return (
        f"Your SQL was rejected: {exc}. "
        "Re-read the GROUP BY rules and the columns list, fix the issue, "
        f"and rewrite as ONE SELECT against {table}. Return PLAN then SQL."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_retry_prompt_includes_error_table_and_output_contract -v
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "refactor: add shared analytics retry prompt"
```

## Task 4: Extract DuckDB Connection And Execution Helpers

**Files:**
- Modify: `openwebui/filter_analytics.py:1129-1303`
- Modify: `tests/test_filter_pipeline.py`
- Test: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append these tests near the `_run_query` tests in `tests/test_filter_pipeline.py`:

```python
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
    from unittest.mock import MagicMock

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_execute_sql_fetches_dict_rows tests/test_filter_pipeline.py::test_build_duckdb_conn_installs_httpfs_creates_secret_and_view -v
```

Expected:

```text
FAILED tests/test_filter_pipeline.py::test_execute_sql_fetches_dict_rows - ImportError: cannot import name '_execute_sql' from 'filter_analytics'
FAILED tests/test_filter_pipeline.py::test_build_duckdb_conn_installs_httpfs_creates_secret_and_view - ImportError: cannot import name '_build_duckdb_conn' from 'filter_analytics'
```

- [ ] **Step 3: Move `duckdb` import to module scope**

Add this import near the top of `openwebui/filter_analytics.py`, with the other imports:

```python
import duckdb
```

- [ ] **Step 4: Implement the helpers**

Add these functions before `_run_query` in `openwebui/filter_analytics.py:1213`:

```python
def _execute_sql(conn, sql_capped: str) -> list[dict]:
    return conn.execute(sql_capped).fetchdf().to_dict(orient="records")


def _build_duckdb_conn(table: str, s3_bucket: str, aws_region: str):
    conn = duckdb.connect(
        config={
            "memory_limit": "512MB",
            "extension_directory": "/tmp/duckdb-extensions",
        }
    )
    path = f"s3://{s3_bucket}/{table}/*.parquet"
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    auth_mode = _create_s3_secret(conn, aws_region)
    print(f"DuckDB S3 auth mode: {auth_mode}; path: {path}")
    conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
    return conn
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_execute_sql_fetches_dict_rows tests/test_filter_pipeline.py::test_build_duckdb_conn_installs_httpfs_creates_secret_and_view -v
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "refactor: extract duckdb analytics helpers"
```

## Task 5: Refactor `_run_query` Retry Loop

**Files:**
- Modify: `openwebui/filter_analytics.py:1194-1303`
- Modify: `tests/test_filter_pipeline.py`
- Test: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append these tests near the `_run_query` tests in `tests/test_filter_pipeline.py`:

```python
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
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
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
        _run_query("top zones", "route_top_pickup_zones", _query_registry(), "analytics-bucket", "ap-southeast-1")

    second_messages = mock_llm.call_args_list[1].args[0]
    assert second_messages[-2]["role"] == "assistant"
    assert second_messages[-2]["content"].startswith("PLAN: Bad group by.")
    assert second_messages[-1]["role"] == "user"
    assert 'Binder Error: column "revenue" must appear in the GROUP BY clause' in second_messages[-1]["content"]


def test_run_query_reuses_connection_across_attempts():
    import duckdb
    from filter_analytics import _run_query
    from unittest.mock import MagicMock

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_run_query_retries_on_duckdb_binder_error tests/test_filter_pipeline.py::test_run_query_retries_on_catalog_error tests/test_filter_pipeline.py::test_run_query_raises_after_two_duckdb_failures tests/test_filter_pipeline.py::test_run_query_validator_then_duckdb_error_in_one_session tests/test_filter_pipeline.py::test_run_query_extracts_plan_and_sql tests/test_filter_pipeline.py::test_run_query_handles_missing_plan_delimiter tests/test_filter_pipeline.py::test_run_query_strips_fences_around_full_plan_and_sql tests/test_filter_pipeline.py::test_run_query_ignores_sql_colon_inside_plan_text tests/test_filter_pipeline.py::test_run_query_retry_message_includes_error_verbatim tests/test_filter_pipeline.py::test_run_query_reuses_connection_across_attempts -v
```

Expected before implementation:

```text
FAILED tests/test_filter_pipeline.py::test_run_query_retries_on_duckdb_binder_error
FAILED tests/test_filter_pipeline.py::test_run_query_retries_on_catalog_error
FAILED tests/test_filter_pipeline.py::test_run_query_raises_after_two_duckdb_failures
FAILED tests/test_filter_pipeline.py::test_run_query_validator_then_duckdb_error_in_one_session
FAILED tests/test_filter_pipeline.py::test_run_query_extracts_plan_and_sql
FAILED tests/test_filter_pipeline.py::test_run_query_handles_missing_plan_delimiter
FAILED tests/test_filter_pipeline.py::test_run_query_strips_fences_around_full_plan_and_sql
FAILED tests/test_filter_pipeline.py::test_run_query_ignores_sql_colon_inside_plan_text
FAILED tests/test_filter_pipeline.py::test_run_query_retry_message_includes_error_verbatim
FAILED tests/test_filter_pipeline.py::test_run_query_reuses_connection_across_attempts
```

- [ ] **Step 3: Replace `_run_query` implementation**

Replace `openwebui/filter_analytics.py:1216-1303` with:

```python
def _run_query(
    question: str,
    table: str,
    registry: dict,
    s3_bucket: str,
    aws_region: str = AWS_REGION,
    litellm_url: str = LITELLM_URL,
    litellm_model: str = LITELLM_MODEL,
    api_key: str = "",
) -> dict:
    """Returns {"sql": str, "plan": str, "rows": list[dict], "capped": bool}."""
    schema = registry[table]
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", aws_region):
        raise ValueError(f"Invalid aws_region format: {aws_region!r}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]", s3_bucket):
        raise ValueError(f"Invalid s3_bucket: {s3_bucket!r}")
    col_text = ", ".join(f"{c['name']} ({c['type']})" for c in schema["columns"])
    messages: list[dict] = [
        {"role": "system", "content": _QUERY_SYSTEM},
        {
            "role": "user",
            "content": f"Table: {table}\nColumns: {col_text}\n\nQuestion: {question}",
        },
    ]

    conn = _build_duckdb_conn(table, s3_bucket, aws_region)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            for attempt in range(2):
                raw = _llm_chat(
                    messages,
                    model=litellm_model,
                    litellm_url=litellm_url,
                    api_key=api_key,
                )
                stripped = _strip_fences(raw)
                plan, sql = _split_plan_and_sql(stripped)
                sql = _normalize_duckdb_sql(sql.rstrip(";").strip())

                try:
                    _validate_sql(sql, table, set(registry.keys()))
                    sql_capped, applied_cap = _wrap_with_limit(sql)
                    future = executor.submit(_execute_sql, conn, sql_capped)
                    try:
                        rows = future.result(timeout=DUCKDB_TIMEOUT)
                    except FuturesTimeoutError:
                        raise TimeoutError(f"DuckDB query exceeded {DUCKDB_TIMEOUT}s")
                    capped = applied_cap and len(rows) > ROW_CAP
                    return {
                        "sql": sql,
                        "plan": plan,
                        "rows": rows[:ROW_CAP],
                        "capped": capped,
                    }
                except (SQLValidationError, duckdb.Error) as exc:
                    if attempt == 1:
                        raise
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": _retry_prompt(exc, table)})
    finally:
        conn.close()

    raise RuntimeError("SQL generation failed without returning a result")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_run_query_retries_on_duckdb_binder_error tests/test_filter_pipeline.py::test_run_query_retries_on_catalog_error tests/test_filter_pipeline.py::test_run_query_raises_after_two_duckdb_failures tests/test_filter_pipeline.py::test_run_query_validator_then_duckdb_error_in_one_session tests/test_filter_pipeline.py::test_run_query_extracts_plan_and_sql tests/test_filter_pipeline.py::test_run_query_handles_missing_plan_delimiter tests/test_filter_pipeline.py::test_run_query_strips_fences_around_full_plan_and_sql tests/test_filter_pipeline.py::test_run_query_ignores_sql_colon_inside_plan_text tests/test_filter_pipeline.py::test_run_query_retry_message_includes_error_verbatim tests/test_filter_pipeline.py::test_run_query_reuses_connection_across_attempts -v
```

Expected:

```text
10 passed
```

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "fix: retry analytics sql on duckdb errors"
```

## Task 6: Stream The PLAN Block

**Files:**
- Modify: `openwebui/filter_analytics.py:1429-1582`
- Modify: `tests/test_filter_pipeline.py:630-1060`
- Test: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Append these async tests near the existing `_stream_analytics` tests:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_stream_analytics_yields_plan_block_between_table_and_sql tests/test_filter_pipeline.py::test_stream_analytics_omits_plan_block_when_empty_or_missing -v
```

Expected:

```text
FAILED tests/test_filter_pipeline.py::test_stream_analytics_yields_plan_block_between_table_and_sql - AssertionError: assert '> **Plan:** Use route_top_pickup_zones at pickup-zone grain.' in ...
1 failed, 1 passed
```

- [ ] **Step 3: Implement plan emission**

In `openwebui/filter_analytics.py:1494-1500`, replace:

```python
    rows = query_result["rows"]
    sql = query_result["sql"]
    capped = query_result["capped"]

    yield f"> **SQL:**\n> ```sql\n> {sql}\n> ```\n"
```

with:

```python
    rows = query_result["rows"]
    sql = query_result["sql"]
    plan = query_result.get("plan", "")
    capped = query_result["capped"]

    if plan:
        yield f"> **Plan:** {plan}\n"
    yield f"> **SQL:**\n> ```sql\n> {sql}\n> ```\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_stream_analytics_yields_plan_block_between_table_and_sql tests/test_filter_pipeline.py::test_stream_analytics_omits_plan_block_when_empty_or_missing -v
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "feat: stream analytics query plan"
```

## Task 7: Rewrite `_QUERY_SYSTEM`

**Files:**
- Modify: `openwebui/filter_analytics.py:1194-1210`
- Modify: `tests/test_filter_intent.py:1-188`
- Test: `tests/test_filter_intent.py`

- [ ] **Step 1: Write the failing tests**

Add `_QUERY_SYSTEM` to the imports in `tests/test_filter_intent.py:4-12`:

```python
from filter_analytics import (
    classify_intent,
    INTENT_ANALYTICS,
    INTENT_AMBIGUOUS,
    INTENT_CHAT,
    _QUERY_SYSTEM,
    _normalize_duckdb_sql,
    _select_presentation_mode,
)
```

Append these tests in `tests/test_filter_intent.py`:

```python
def test_query_system_prompt_contains_group_by_rule():
    assert "GROUP BY" in _QUERY_SYSTEM


def test_query_system_prompt_contains_plan_and_sql_contract():
    assert "PLAN:" in _QUERY_SYSTEM
    assert "SQL:" in _QUERY_SYSTEM
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_intent.py::test_query_system_prompt_contains_group_by_rule tests/test_filter_intent.py::test_query_system_prompt_contains_plan_and_sql_contract -v
```

Expected:

```text
FAILED tests/test_filter_intent.py::test_query_system_prompt_contains_group_by_rule - AssertionError
FAILED tests/test_filter_intent.py::test_query_system_prompt_contains_plan_and_sql_contract - AssertionError
```

- [ ] **Step 3: Replace `_QUERY_SYSTEM`**

Replace `openwebui/filter_analytics.py:1194-1210` with:

```python
_QUERY_SYSTEM = """You are a SQL query agent for NYC yellow cab trip analytics on DuckDB reading Parquet files on S3.

OUTPUT CONTRACT
First, write a short PLAN paragraph (2-4 lines) covering:
  - which columns from the table answer the question
  - the grain you are answering at (row-level vs aggregated)
  - any aggregation/GROUP BY you intend to use
  - if the question conflicts (e.g. asks for two grains), which one you chose and why
Then, on a new line, write "SQL:" followed by ONE SELECT statement.

GROUP BY RULES
- Every non-aggregated column in SELECT must appear in GROUP BY.
- If a column is already a measure on a pre-aggregated table (revenue, trip_count, avg_fare etc. on kpi_*/route_*/ops_*), do NOT re-aggregate unless rolling up to a coarser grain.
- When rolling up: SUM measures, AVG only ratios with care, COUNT(*) for trip_count rollups.

DUCKDB DIALECT
- Recent windows: CURRENT_DATE - INTERVAL 7 DAY (not DATE_SUB)
- Date parts: EXTRACT(month FROM date_col)
- No read_parquet(), httpfs, COPY, or file functions
- One SELECT statement, no semicolons, no DDL

DOMAIN
- Borough names: Manhattan, Brooklyn, Queens, Bronx, Staten Island
- Peak hours: 7-9 and 17-20 (24h)
- The revenue column is called `revenue` on most tables (16 tables) and `total_revenue` on a few (`fact_trips_daily`, `fact_trips_hourly_zone`, `kpi_monthly_summary`, `dq_*`). Use the exact name shown in the per-query Columns list.
- Pre-aggregated tables (`kpi_*`/`route_*`/`ops_*`/`fact_trips_borough`) already contain summed measures - select directly, do not re-aggregate unless rolling up to a coarser grain.

EXAMPLES

Q: top 20 pickup zones by total revenue, with a borough breakdown chart
Table: route_top_pickup_zones
PLAN: route_top_pickup_zones is pre-aggregated at zone grain. The user asked for top 20 zones AND a borough chart - conflicting grain. I'll answer at zone grain (more specific) and keep pickup_borough so the chart agent can group it downstream.
SQL:
SELECT pickup_zone, pickup_borough, revenue
FROM route_top_pickup_zones
ORDER BY revenue DESC
LIMIT 20

Q: weekly revenue trend over the last 8 weeks
Table: fact_trips_daily
PLAN: fact_trips_daily is at day grain. Need to roll up to weeks and aggregate revenue. Use DATE_TRUNC for the week bucket and a recent window filter.
SQL:
SELECT DATE_TRUNC('week', pickup_date) AS week,
       SUM(total_revenue) AS revenue
FROM fact_trips_daily
WHERE pickup_date >= CURRENT_DATE - INTERVAL 56 DAY
GROUP BY 1
ORDER BY 1"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_filter_intent.py::test_query_system_prompt_contains_group_by_rule tests/test_filter_intent.py::test_query_system_prompt_contains_plan_and_sql_contract -v
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_intent.py
git commit -m "feat: strengthen analytics sql prompt"
```

## Task 8: Curate Schema Registry Metadata

**Files:**
- Modify: `schema_registry.json:378-427`
- Modify: `schema_registry.json:428-487`
- Modify: `schema_registry.json:488-542`
- Modify: `schema_registry.json:543-602`
- Modify: `schema_registry.json:603-655`
- Modify: `schema_registry.json:656-719`
- Modify: `schema_registry.json:783-835`
- Modify: `schema_registry.json:1058-1145`
- Modify: `schema_registry.json:1673-1720`
- Create: `tests/test_schema_registry_curated.py`
- Test: `tests/test_schema_registry_curated.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schema_registry_curated.py`:

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import _registry_as_prompt


CURATED_TABLES = [
    "route_top_pickup_zones",
    "kpi_zone_performance",
    "fact_trips_daily",
    "fact_trips_hourly",
    "fact_trips_hourly_zone",
    "fact_trips_borough",
    "kpi_daily_overview",
    "kpi_borough_comparison",
    "kpi_payment_trends",
]


def _load_registry():
    path = Path(__file__).parent.parent / "schema_registry.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_curated_tables_have_required_metadata():
    registry = _load_registry()

    for table in CURATED_TABLES:
        entry = registry[table]
        assert entry["description"]
        assert entry["aliases"]
        assert entry["grain"]
        assert entry["use_for"]
        assert entry["avoid_for"]
        assert entry["example_questions"]
        assert entry["metadata_source"]["description"] == "curated"
        assert entry["metadata_source"]["aliases"] == "curated"
        assert entry["metadata_source"]["grain"] == "curated"
        assert entry["metadata_source"]["use_for"] == "curated"
        assert entry["metadata_source"]["avoid_for"] == "curated"
        assert entry["metadata_source"]["example_questions"] == "curated"


def test_curated_tables_appear_in_registry_prompt():
    registry = _load_registry()
    prompt = _registry_as_prompt({table: registry[table] for table in CURATED_TABLES})

    for table in CURATED_TABLES:
        entry = registry[table]
        assert table in prompt
        assert f"aliases: {', '.join(entry['aliases'])}" in prompt
        assert f"grain: {entry['grain']}" in prompt
        assert "use_for:" in prompt
        assert "avoid_for:" in prompt
        assert "examples:" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_schema_registry_curated.py -v
```

Expected:

```text
FAILED tests/test_schema_registry_curated.py::test_curated_tables_have_required_metadata - KeyError: 'aliases'
```

- [ ] **Step 3: Curate `route_top_pickup_zones`**

In `schema_registry.json:1673-1720`, replace `description`, `example_questions`, and `metadata_source`, then add `aliases`, `grain`, `use_for`, and `avoid_for` so the entry contains:

```json
    "description": "Top pickup zones pre-aggregated at zone grain with borough context.",
    "example_questions": [
      "top 20 pickup zones by revenue",
      "busiest pickup zones in Manhattan"
    ],
    "dimensions": [
      "pickup_zone",
      "pickup_borough"
    ],
    "measures": [
      "trip_count",
      "revenue",
      "avg_fare",
      "avg_distance"
    ],
    "date_columns": [],
    "metadata_source": {
      "columns": "schema",
      "dimensions": "derived",
      "measures": "derived",
      "date_columns": "derived",
      "description": "curated",
      "aliases": "curated",
      "grain": "curated",
      "use_for": "curated",
      "avoid_for": "curated",
      "example_questions": "curated"
    },
    "aliases": [
      "top pickup zones",
      "busiest pickup zones",
      "popular pickup zones"
    ],
    "grain": "one row per pickup zone",
    "use_for": [
      "ranking pickup zones by trips/revenue",
      "zone leaderboards with borough context"
    ],
    "avoid_for": [
      "daily/hourly trends (no date column)",
      "borough-only rollups (use kpi_borough_comparison)"
    ]
```

- [ ] **Step 4: Curate the remaining eight tables**

Apply these exact field values while preserving each table's existing `columns`, `dimensions`, `measures`, and `date_columns` arrays:

```json
{
  "fact_trips_borough": {
    "description": "Daily pickup-borough trip counts, revenue, and fare metrics.",
    "aliases": ["borough trips by day", "daily borough revenue", "pickup borough daily"],
    "grain": "one row per pickup date and pickup borough",
    "use_for": ["daily borough trends", "pickup borough revenue by date", "borough-level trip counts over time"],
    "avoid_for": ["zone-level rankings because this table has no zone column", "payment-type analysis because this table has no payment fields"],
    "example_questions": ["daily revenue by pickup borough", "trip count trend for Manhattan by day"]
  },
  "fact_trips_daily": {
    "description": "Daily trip, revenue, fare, tip, distance, and passenger metrics.",
    "aliases": ["daily trips", "daily revenue", "taxi daily facts"],
    "grain": "one row per pickup date",
    "use_for": ["daily revenue trends", "weekly or monthly rollups from day grain", "overall taxi demand over time"],
    "avoid_for": ["borough comparisons because this table has no borough column", "zone rankings because this table has no zone column"],
    "example_questions": ["weekly revenue trend over the last 8 weeks", "daily taxi trips and revenue"]
  },
  "fact_trips_hourly": {
    "description": "Hourly trip, revenue, fare, tip, and distance metrics by pickup date and hour.",
    "aliases": ["hourly trips", "hourly revenue", "pickup hour facts"],
    "grain": "one row per pickup date and pickup hour",
    "use_for": ["hourly demand trends", "peak-hour analysis over time", "hour-of-day revenue patterns"],
    "avoid_for": ["zone-level hourly analysis because this table has no zone column", "borough analysis because this table has no borough column"],
    "example_questions": ["hourly revenue trend for taxi trips", "which pickup hours have the most trips"]
  },
  "fact_trips_hourly_zone": {
    "description": "Hourly pickup-zone metrics with borough context, trip counts, revenue, fare, and dropoff counts.",
    "aliases": ["hourly zone trips", "zone hourly revenue", "pickup zone hourly facts"],
    "grain": "one row per pickup date, pickup hour, pickup zone, and pickup borough",
    "use_for": ["zone-level hourly patterns", "peak-hour zone comparisons", "hourly borough context at zone grain"],
    "avoid_for": ["payment-type questions because this table has no payment fields", "overall daily trends when zone detail is unnecessary"],
    "example_questions": ["top pickup zones by revenue during peak hours", "hourly trips by pickup zone and borough"]
  },
  "kpi_borough_comparison": {
    "description": "Pre-aggregated pickup-borough comparison metrics for trips, revenue, market share, fares, tips, and distance.",
    "aliases": ["borough comparison", "borough revenue", "borough market share"],
    "grain": "one row per pickup borough",
    "use_for": ["borough-level rankings", "borough market-share comparisons", "overall revenue by pickup borough"],
    "avoid_for": ["daily borough trends because this table has no date column", "zone rankings because this table has no zone column"],
    "example_questions": ["compare revenue by pickup borough", "which borough has the highest taxi market share"]
  },
  "kpi_daily_overview": {
    "description": "Daily KPI overview for trips, revenue, fare, tip, distance, vendors, and utilization.",
    "aliases": ["daily overview", "daily kpi", "daily taxi summary"],
    "grain": "one row per pickup date",
    "use_for": ["daily KPI summaries", "overall daily revenue and utilization", "recent daily taxi performance"],
    "avoid_for": ["borough-level questions because this table has no borough column", "payment mix because this table has no payment fields"],
    "example_questions": ["show daily taxi revenue overview", "recent daily utilization and trips"]
  },
  "kpi_payment_trends": {
    "description": "Pre-aggregated payment-type metrics for trips, revenue, fare, tip, and tip percentage.",
    "aliases": ["payment trends", "payment type revenue", "taxi payment mix"],
    "grain": "one row per payment type",
    "use_for": ["payment-type comparisons", "tip behavior by payment type", "revenue split by payment method"],
    "avoid_for": ["daily payment trends because this table has no date column", "borough or zone analysis because this table has no geography columns"],
    "example_questions": ["revenue by payment type", "which payment type has the highest average tip percentage"]
  },
  "kpi_zone_performance": {
    "description": "Pre-aggregated zone performance metrics for pickups, dropoffs, net flow, revenue, fare, tip, and airport trips.",
    "aliases": ["zone performance", "pickup dropoff zone metrics", "taxi zone performance"],
    "grain": "one row per taxi zone",
    "use_for": ["zone pickup/dropoff performance", "net flow by zone", "pickup revenue versus dropoff revenue by zone"],
    "avoid_for": ["daily trends because this table has no date column", "hourly trends because this table has no hour column"],
    "example_questions": ["which zones have the most pickups", "show pickup revenue and dropoff revenue by zone"]
  }
}
```

For each of the eight entries, extend `metadata_source` to this shape:

```json
    "metadata_source": {
      "columns": "schema",
      "dimensions": "derived",
      "measures": "derived",
      "date_columns": "derived",
      "description": "curated",
      "aliases": "curated",
      "grain": "curated",
      "use_for": "curated",
      "avoid_for": "curated",
      "example_questions": "curated"
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
.venv/bin/pytest tests/test_schema_registry_curated.py -v
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Commit**

```bash
git add schema_registry.json tests/test_schema_registry_curated.py
git commit -m "feat: curate analytics registry metadata"
```

## Task 9: End-To-End Pickup Borough Regression

**Files:**
- Modify: `tests/test_filter_pipeline.py:630-1060`
- Test: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write the failing regression test**

Append this test near the existing `_stream_analytics` integration tests:

```python
@pytest.mark.asyncio
async def test_stream_analytics_pickup_borough_regression():
    import duckdb

    registry = {
        "route_top_pickup_zones": {
            "description": "Top pickup zones pre-aggregated at zone grain with borough context.",
            "tier": "route",
            "columns": [
                {"name": "pickup_zone", "type": "string"},
                {"name": "pickup_borough", "type": "string"},
                {"name": "revenue", "type": "double"},
            ],
            "aliases": ["top pickup zones"],
            "example_questions": ["top 20 pickup zones by revenue"],
            "grain": "one row per pickup zone",
            "use_for": ["ranking pickup zones by trips/revenue"],
            "avoid_for": ["borough-only rollups (use kpi_borough_comparison)"],
        }
    }
    prompt = (
        "List the top 20 pickup zones by total taxi revenue, represent a chart "
        "of total revenue following pickup borough and conclude it"
    )
    first_llm = (
        "PLAN: Incorrectly roll up to borough while selecting bare revenue.\n"
        "SQL:\n"
        "SELECT pickup_borough, revenue FROM route_top_pickup_zones GROUP BY pickup_borough"
    )
    second_llm = (
        "PLAN: route_top_pickup_zones is already at pickup-zone grain. Keep pickup_borough "
        "for the chart while ranking zones by revenue.\n"
        "SQL:\n"
        "SELECT pickup_zone, pickup_borough, revenue FROM route_top_pickup_zones "
        "ORDER BY revenue DESC LIMIT 20"
    )
    rows = [{"pickup_zone": "Midtown Center", "pickup_borough": "Manhattan", "revenue": 2500.0}]

    async def fake_summary(*args, **kwargs):
        yield "Midtown Center leads revenue."

    with patch("filter_analytics._load_registry", return_value=registry), \
         patch("filter_analytics._run_supervisor", return_value={"table": "route_top_pickup_zones", "confidence": "high", "reasoning": "Top pickup zones match"}), \
         patch("filter_analytics._llm_chat", side_effect=[first_llm, second_llm]), \
         patch("filter_analytics._build_duckdb_conn") as mock_build, \
         patch("filter_analytics._execute_sql", side_effect=[
             duckdb.BinderException('Binder Error: column "revenue" must appear in the GROUP BY clause'),
             rows,
         ]), \
         patch("filter_analytics._run_chart_spec", return_value={"type": "bar", "x": "pickup_borough", "y": "revenue"}), \
         patch("filter_analytics.build_html_artifact", return_value="<html>chart</html>"), \
         patch("filter_analytics._stream_summary", side_effect=fake_summary):
        mock_build.return_value = type("Conn", (), {"close": lambda self: None})()
        chunks = []
        async for chunk in _stream_analytics(
            prompt,
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
    assert "> **Error:**" not in response
    assert "> **Plan:** route_top_pickup_zones is already at pickup-zone grain." in response
    assert (
        "SELECT pickup_zone, pickup_borough, revenue FROM route_top_pickup_zones "
        "ORDER BY revenue DESC LIMIT 20"
    ) in response
    assert "Midtown Center leads revenue." in response
```

- [ ] **Step 2: Run test to verify it passes with the completed implementation**

This regression test should fail on the original code, but after Tasks 1-8 it should pass. Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py::test_stream_analytics_pickup_borough_regression -v
```

Expected:

```text
1 passed
```

- [ ] **Step 3: Commit**

```bash
git add tests/test_filter_pipeline.py
git commit -m "test: cover pickup borough sql retry regression"
```

## Task 10: Full Verification And Manual End-To-End Check

**Files:**
- Verify: `openwebui/filter_analytics.py`
- Verify: `tests/test_filter_pipeline.py`
- Verify: `tests/test_filter_intent.py`
- Verify: `tests/test_schema_registry_curated.py`
- Verify: `schema_registry.json`

- [ ] **Step 1: Run the focused test files**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py tests/test_filter_intent.py tests/test_schema_registry_curated.py -v
```

Expected:

```text
collected 91 items
...
91 passed
```

If the collected count differs because new unrelated tests landed, require all tests in these three files to pass.

- [ ] **Step 2: Run the full suite**

Run:

```bash
ANALYTICS_S3_BUCKET=nyc-taxi-analytics-dev .venv/bin/pytest tests/ -v
```

Expected:

```text
91 passed
```

If collection includes additional tests beyond the current target, require all tests under `tests/` to pass.

- [ ] **Step 3: Manual end-to-end verification**

Run the Open WebUI analytics pipe against the original failing prompt:

```text
List the top 20 pickup zones by total taxi revenue, represent a chart of total revenue following pickup borough and conclude it
```

Expected user-visible stream order:

```text
> **Table:** `route_top_pickup_zones` ...
> **Plan:** route_top_pickup_zones is pre-aggregated at zone grain...
> **SQL:**
> ```sql
> SELECT pickup_zone, pickup_borough, revenue
> FROM route_top_pickup_zones
> ORDER BY revenue DESC
> LIMIT 20
> ```
> **Result:** 20 rows ...
```

Expected behavior:

```text
No DuckDB BinderException reaches the user.
The summary is produced.
The requested chart renders.
```

- [ ] **Step 4: Inspect changed files**

Run:

```bash
git diff -- openwebui/filter_analytics.py tests/test_filter_pipeline.py tests/test_filter_intent.py tests/test_schema_registry_curated.py schema_registry.json
```

Expected:

```text
Diff only includes the SQL prompt/retry implementation, plan streaming, registry curation, and tests described in this plan.
No Pipe.pipe routing changes.
No _validate_sql rule changes.
No dependency file changes.
No Co-Authored-By trailer appears in commit messages.
```

- [ ] **Step 5: Confirm the worktree is ready for handoff**

Run:

```bash
git status --short
```

Expected:

```text
No uncommitted changes from this implementation remain after the task commits above. Ignore unrelated pre-existing worktree changes that are outside this feature.
```

## Self-Review Checklist

- [x] Spec coverage: the plan covers `_split_plan_and_sql`, `_wrap_with_limit`, `_retry_prompt`, `_execute_sql`, `_build_duckdb_conn`, `_run_query` retry on `SQLValidationError` and `duckdb.Error`, plan emission, `_QUERY_SYSTEM`, nine-table registry curation, the pickup-borough regression, and full/manual verification.
- [x] Placeholder scan: no banned placeholder text or incomplete test instructions remain.
- [x] Type/name consistency: helper names are consistent throughout: `_split_plan_and_sql`, `_wrap_with_limit`, `_retry_prompt`, `_execute_sql`, `_build_duckdb_conn`, `_run_query`.
- [x] Compatibility check: streaming reads `query_result.get("plan", "")`, so the existing eight `_run_query` mocks that omit `plan` stay green.
- [x] Retry boundary check: only `SQLValidationError` and `duckdb.Error` are retried; `FuturesTimeoutError` still maps to `TimeoutError` outside the retry set.
