# Open WebUI Filter Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a self-contained Open WebUI Filter that intercepts messages in `private-chat`, routes analytics questions through the NYC taxi pipeline, and returns chart + summary artifacts — without touching Helm, ArgoCD, or any existing service.

**Architecture:** A single Python file (`openwebui/filter_analytics.py`) is loaded via the Open WebUI admin panel as a Function/Filter. It classifies each message with a two-tier keyword router (domain terms + analytics words), inlines the supervisor → query → summarize agent logic using direct `httpx` calls to Ollama and `duckdb` for S3 queries, and returns an HTML artifact containing an embedded Vega-Lite chart when the pipeline produces one.

**Tech Stack:** Python 3.12, Open WebUI Functions API (inlet/outlet/Valves pattern), httpx (pre-installed in Open WebUI), duckdb (declared as pip requirement in filter), Vega-Embed CDN for chart rendering, pytest for unit tests.

---

## File Map

| File | Status | Responsibility |
|---|---|---|
| `openwebui/filter_analytics.py` | Create | Self-contained Filter: intent router, inlined pipeline, HTML artifact builder |
| `tests/test_filter_intent.py` | Create | Unit tests for classify_intent() and build_html_artifact() |
| `tests/test_filter_pipeline.py` | Create | Unit tests for inlined supervisor/query/summarize helpers |

No existing files are modified.

---

## Task 1: Intent Classifier + Unit Tests

**Files:**
- Create: `openwebui/filter_analytics.py` (classify_intent only, no pipeline yet)
- Create: `tests/test_filter_intent.py`

- [ ] **Step 1: Create the openwebui directory and stub filter file**

```bash
mkdir -p openwebui
```

Create `openwebui/filter_analytics.py` with this content:

```python
"""
title: NYC Taxi Analytics Filter
author: llmops
version: 1.0.0
license: MIT
requirements: duckdb==1.2.2
"""

from __future__ import annotations

DOMAIN_TERMS = {
    "taxi", "trip", "trips", "fare", "borough", "zone", "pickup", "dropoff",
    "vendor", "route", "revenue", "passenger", "passengers", "yellow", "green",
    "fhv", "manhattan", "brooklyn", "queens", "bronx", "staten island",
}

ANALYTICS_WORDS = {
    "how many", "average", "total", "compare", "top", "trend", "count",
    "per", "rate", "show", "summary", "breakdown", "most", "least", "peak",
    "weekly", "monthly", "daily", "hourly",
}

INTENT_ANALYTICS = "analytics"
INTENT_AMBIGUOUS = "ambiguous"
INTENT_CHAT = "chat"


def classify_intent(message: str) -> str:
    """Three-tier intent classification based on domain + analytics signal counts."""
    lower = message.lower()

    domain_count = sum(1 for term in DOMAIN_TERMS if term in lower)

    analytics_count = sum(1 for word in ANALYTICS_WORDS if word in lower)

    if domain_count >= 1 and analytics_count >= 1:
        return INTENT_ANALYTICS
    if domain_count >= 1:
        return INTENT_AMBIGUOUS
    return INTENT_CHAT
```

- [ ] **Step 2: Write failing tests for classify_intent**

Create `tests/test_filter_intent.py`:

```python
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
from filter_analytics import classify_intent, INTENT_ANALYTICS, INTENT_AMBIGUOUS, INTENT_CHAT


def test_routes_analytics_on_domain_plus_analytics_signal():
    assert classify_intent("show monthly revenue trend for taxi trips") == INTENT_ANALYTICS


def test_routes_analytics_on_borough_and_total():
    assert classify_intent("what is the total fare by borough") == INTENT_ANALYTICS


def test_routes_analytics_on_top_zones():
    assert classify_intent("top pickup zones by revenue") == INTENT_ANALYTICS


def test_routes_ambiguous_on_domain_only():
    assert classify_intent("what about taxi") == INTENT_AMBIGUOUS


def test_routes_ambiguous_on_borough_only():
    assert classify_intent("tell me about manhattan") == INTENT_AMBIGUOUS


def test_routes_chat_on_no_domain_signal():
    assert classify_intent("how do I write a Python function") == INTENT_CHAT


def test_routes_chat_on_generic_greeting():
    assert classify_intent("hello, how are you?") == INTENT_CHAT


def test_false_positive_total_without_domain():
    assert classify_intent("what is the total cost of this project") == INTENT_CHAT


def test_false_positive_compare_without_domain():
    assert classify_intent("compare these two code snippets") == INTENT_CHAT


def test_routes_analytics_on_peak_hours():
    assert classify_intent("show peak hour trips daily") == INTENT_ANALYTICS


def test_routes_analytics_case_insensitive():
    assert classify_intent("SHOW MONTHLY REVENUE FOR TAXI") == INTENT_ANALYTICS
```

- [ ] **Step 3: Run tests to confirm they fail (filter_analytics not yet importable from tests)**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_intent.py -v 2>&1 | head -30
```

Expected: some tests fail with import errors or assertion errors until classify_intent is correct.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_intent.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_intent.py
git commit -m "feat: intent classifier with three-tier routing and unit tests"
```

---

## Task 2: HTML Artifact Builder + Unit Tests

**Files:**
- Modify: `openwebui/filter_analytics.py` — add `build_html_artifact()` and `chart_spec_to_vegalite()`
- Modify: `tests/test_filter_intent.py` — add artifact tests

- [ ] **Step 1: Write failing tests for artifact builder**

Append to `tests/test_filter_intent.py`:

```python
from filter_analytics import build_html_artifact, chart_spec_to_vegalite


def test_html_artifact_built_from_bar_chart_spec():
    chart_spec = {"type": "bar", "x": "month", "y": "revenue", "series": []}
    rows = [{"month": "Jan", "revenue": 1000}]
    html = build_html_artifact(chart_spec, rows)
    assert "<!DOCTYPE html>" in html
    assert "vegaEmbed" in html
    assert '"mark": "bar"' in html


def test_html_artifact_built_from_line_chart_spec():
    chart_spec = {"type": "line", "x": "date", "y": "trip_count", "series": []}
    rows = [{"date": "2023-01", "trip_count": 500}]
    html = build_html_artifact(chart_spec, rows)
    assert '"mark": "line"' in html


def test_html_artifact_pie_renders_as_horizontal_bar():
    chart_spec = {"type": "pie", "x": "borough", "y": "fare", "series": []}
    rows = [{"borough": "Manhattan", "fare": 5000}]
    html = build_html_artifact(chart_spec, rows)
    assert '"mark": "bar"' in html


def test_no_html_when_chart_type_is_table():
    chart_spec = {"type": "table", "x": "month", "y": "revenue", "series": []}
    rows = [{"month": "Jan", "revenue": 1000}]
    result = build_html_artifact(chart_spec, rows)
    assert result is None


def test_chart_spec_to_vegalite_bar():
    spec = chart_spec_to_vegalite({"type": "bar", "x": "month", "y": "revenue"}, [{"month": "Jan", "revenue": 100}])
    assert spec["mark"] == "bar"
    assert spec["encoding"]["x"]["field"] == "month"
    assert spec["encoding"]["y"]["field"] == "revenue"
    assert spec["encoding"]["x"]["type"] == "ordinal"
    assert spec["encoding"]["y"]["type"] == "quantitative"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_intent.py -v -k "artifact or vegalite" 2>&1 | head -20
```

Expected: ImportError — `build_html_artifact` and `chart_spec_to_vegalite` not yet defined.

- [ ] **Step 3: Add chart_spec_to_vegalite and build_html_artifact to the filter**

Append to `openwebui/filter_analytics.py` after the intent classifier:

```python
import json


def chart_spec_to_vegalite(chart_spec: dict, rows: list[dict]) -> dict:
    """Convert summarize agent's custom chart_spec to a Vega-Lite spec."""
    chart_type = chart_spec.get("type", "bar")
    x_field = chart_spec["x"]
    y_field = chart_spec["y"]

    if chart_type == "line":
        mark = "line"
    else:
        mark = "bar"  # bar and pie both render as bar

    x_orient = {}
    if chart_type == "pie":
        x_orient = {"sort": "-y"}

    return {
        "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
        "mark": mark,
        "data": {"values": rows},
        "encoding": {
            "x": {"field": x_field, "type": "ordinal", **x_orient},
            "y": {"field": y_field, "type": "quantitative"},
        },
        "width": "container",
        "height": 300,
    }


def build_html_artifact(chart_spec: dict, rows: list[dict]) -> str | None:
    """Wrap a Vega-Lite spec in a self-contained HTML artifact string.

    Returns None for table type (text-only result).
    """
    if chart_spec.get("type") == "table":
        return None

    vl_spec = chart_spec_to_vegalite(chart_spec, rows)
    spec_json = json.dumps(vl_spec)

    return f"""<!DOCTYPE html>
<html>
<head>
  <script src="https://cdn.jsdelivr.net/npm/vega@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-lite@5"></script>
  <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
  <style>body {{ margin: 0; }} #chart {{ width: 100%; }}</style>
</head>
<body>
  <div id="chart"></div>
  <script>
    vegaEmbed('#chart', {spec_json}, {{actions: false}});
  </script>
</body>
</html>"""
```

- [ ] **Step 4: Run all filter tests**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_intent.py -v
```

Expected: all 17 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_intent.py
git commit -m "feat: chart_spec_to_vegalite and HTML artifact builder"
```

---

## Task 3: Inlined Pipeline Helpers

**Files:**
- Modify: `openwebui/filter_analytics.py` — add `_ollama_chat()`, `_strip_fences()`, `_validate_sql()`, `_run_supervisor()`, `_run_query()`, `_run_summarize()`
- Create: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Write failing tests for pipeline helpers**

Create `tests/test_filter_pipeline.py`:

```python
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_pipeline.py -v 2>&1 | head -20
```

Expected: ImportError — `_strip_fences`, `_validate_sql`, `SQLValidationError` not yet defined.

- [ ] **Step 3: Add strip_fences, SQLValidationError, and validate_sql to filter**

Append to `openwebui/filter_analytics.py`:

```python
import re

_DDL_KEYWORDS = re.compile(
    r"\b(DROP|CREATE|INSERT|UPDATE|DELETE|ALTER|TRUNCATE)\b", re.IGNORECASE
)
_FILE_FUNCTIONS = re.compile(
    r"\b(read_parquet|read_csv_auto|read_json|COPY|EXPORT|httpfs)\b", re.IGNORECASE
)


class SQLValidationError(Exception):
    pass


def _strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:sql)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    return match.group(1).strip() if match else text


def _validate_sql(sql: str, expected_table: str, known_tables: set) -> None:
    stripped = sql.strip()
    if _FILE_FUNCTIONS.search(stripped):
        raise SQLValidationError("file function not allowed (read_parquet, httpfs, COPY, etc.)")
    if not stripped.upper().startswith("SELECT"):
        raise SQLValidationError("SQL must start with SELECT")
    if _DDL_KEYWORDS.search(stripped):
        raise SQLValidationError("DDL keywords not allowed")
    if ";" in stripped:
        raise SQLValidationError("chained statements not allowed")
    found = set(re.findall(r"\bFROM\s+(\w+)", stripped, re.IGNORECASE))
    found |= set(re.findall(r"\bJOIN\s+(\w+)", stripped, re.IGNORECASE))
    for t in found:
        if t.lower() != expected_table.lower():
            raise SQLValidationError(f"Table '{t}' not allowed — expected '{expected_table}'")
```

- [ ] **Step 4: Run pipeline helper tests**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_pipeline.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Add Ollama HTTP helper to filter**

Append to `openwebui/filter_analytics.py`:

```python
import httpx

OLLAMA_URL = "http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions"
OLLAMA_MODEL = "qwen2.5-coder:7b"
OLLAMA_TIMEOUT = 60


def _ollama_chat(messages: list[dict], model: str = OLLAMA_MODEL, ollama_url: str = OLLAMA_URL) -> str:
    """Direct HTTP call to Ollama OpenAI-compatible endpoint."""
    resp = httpx.post(
        ollama_url,
        json={"model": model, "messages": messages},
        timeout=OLLAMA_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 6: Commit**

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "feat: inlined sql validator, strip_fences, ollama http helper"
```

---

## Task 4: Inlined Supervisor, Query, and Summarize Agents

**Files:**
- Modify: `openwebui/filter_analytics.py` — add `_run_supervisor()`, `_run_query()`, `_run_summarize()`, `REGISTRY` constant

- [ ] **Step 1: Add the registry constant and supervisor agent**

Append to `openwebui/filter_analytics.py`:

```python
# Registry bundled as constant — matches schema_registry.json at repo root.
# Update this dict when schema_registry.json changes.
REGISTRY: dict = {
```

Then run this to generate the dict body and append it:

```bash
python3 -c "
import json
with open('schema_registry.json') as f:
    reg = json.load(f)
print(json.dumps(reg, indent=2))
" >> /tmp/registry_body.txt
```

Copy the JSON output into `filter_analytics.py` as the dict value, converting JSON syntax to Python (replace `true`→`True`, `false`→`False`, `null`→`None`). The final result should be:

```python
REGISTRY: dict = {
    "dim_date": {
        "description": "Dim Date — auto-generated, update manually",
        "tier": "dim",
        "columns": [{"name": "date", "type": "date32[day]"}, ...],
        ...
    },
    # ... all 32 tables
}
```

Then append the supervisor agent:

```python
_SUPERVISOR_SYSTEM = """You are a table selection agent for NYC yellow cab trip analytics.

Dataset tiers:
- kpi: pre-aggregated monthly/weekly/daily metrics — prefer these for summary questions
- fact: daily/hourly grain with zone and vendor IDs — use for detailed filtering
- dim: lookup tables (zone names, boroughs, vendors)
- route: pickup-to-dropoff zone pair aggregates
- ops: operational patterns (peak hours, passenger counts, distances)
- dq: data quality checks — only if asked about data quality

Borough names: Manhattan, Brooklyn, Queens, Bronx, Staten Island.
Revenue = total_fare_amount (excludes tips). Peak hours = 7-9am and 5-8pm.

Select ONE table. Output ONLY valid JSON, no explanation:
{"table": "<table_name>", "confidence": "high|low", "reasoning": "<one sentence>"}"""


def _registry_as_prompt(registry: dict) -> str:
    lines = []
    for table, entry in registry.items():
        col_list = ", ".join(f"{c['name']}({c['type']})" for c in entry["columns"])
        examples = "; ".join(entry.get("example_questions", []))
        lines.append(
            f"- {table} [{entry['tier']}]: {entry['description']} | columns: {col_list} | examples: {examples}"
        )
    return "\n".join(lines)


def _run_supervisor(question: str, registry: dict, ollama_url: str = OLLAMA_URL) -> dict:
    """Returns {"table": str, "confidence": "high|low", "reasoning": str}."""
    registry_text = _registry_as_prompt(registry)
    messages = [
        {"role": "system", "content": _SUPERVISOR_SYSTEM},
        {"role": "user", "content": f"Available tables:\n{registry_text}\n\nQuestion: {question}"},
    ]
    raw = _ollama_chat(messages, ollama_url=ollama_url)
    cleaned = _strip_fences(raw)
    parsed = json.loads(cleaned.strip())
    table = parsed.get("table", "")
    if table not in registry:
        raise ValueError(f"Supervisor selected unknown table: {table}")
    confidence = parsed.get("confidence", "low")
    if confidence not in ("high", "low"):
        confidence = "low"
    return {"table": table, "confidence": confidence, "reasoning": parsed.get("reasoning", "")}
```

- [ ] **Step 2: Add query agent**

Append to `openwebui/filter_analytics.py`:

```python
import signal

S3_BUCKET = "YOUR_BUCKET_NAME"  # overridden by Valves at runtime
AWS_REGION = "ap-southeast-1"
ROW_CAP = 200
DUCKDB_TIMEOUT = 30

_QUERY_SYSTEM = """You are a SQL query agent for NYC yellow cab trip analytics stored in Parquet files on S3.
Rules:
- Write ONE SELECT statement only
- No markdown, no explanation, just the SQL
- Use only the table and columns provided
- Revenue = total_fare_amount (excludes tips)
- Borough values: Manhattan, Brooklyn, Queens, Bronx, Staten Island
- Peak hours: 7-9 and 17-20 (24h)
- Do not use read_parquet(), httpfs, or any file functions"""


def _run_query(question: str, table: str, registry: dict, s3_bucket: str, ollama_url: str = OLLAMA_URL) -> dict:
    """Returns {"sql": str, "rows": list[dict], "capped": bool}."""
    schema = registry[table]
    col_text = ", ".join(f"{c['name']} ({c['type']})" for c in schema["columns"])
    messages = [
        {"role": "system", "content": _QUERY_SYSTEM},
        {"role": "user", "content": f"Table: {table}\nColumns: {col_text}\n\nQuestion: {question}"},
    ]
    raw = _ollama_chat(messages, ollama_url=ollama_url)
    sql = _strip_fences(raw)
    _validate_sql(sql, table, set(registry.keys()))

    import duckdb

    def _timeout(signum, frame):
        raise TimeoutError(f"DuckDB query exceeded {DUCKDB_TIMEOUT}s")

    signal.signal(signal.SIGALRM, _timeout)
    signal.alarm(DUCKDB_TIMEOUT)
    try:
        path = f"s3://{s3_bucket}/{table}/*.parquet"
        conn = duckdb.connect()
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute(f"SET s3_region='{AWS_REGION}';")
        conn.execute("SET s3_use_credential_chain=true;")
        conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
        rows = conn.execute(sql).fetchdf().to_dict(orient="records")
    finally:
        signal.alarm(0)

    before_cap = len(rows)
    return {"sql": sql, "rows": rows[:ROW_CAP], "capped": before_cap > ROW_CAP}
```

- [ ] **Step 3: Add summarize agent**

Append to `openwebui/filter_analytics.py`:

```python
_SUMMARIZE_SYSTEM = """You are a business analytics summarizer for NYC yellow cab trip data.
Given a question and query result rows, output ONLY valid JSON:
{
  "summary": "<2-4 sentence business summary>",
  "chart_spec": {"type": "bar|line|pie|table", "x": "<column>", "y": "<column>", "series": []},
  "capped": <true if rows were capped, else false>
}
Rules:
- summary must be 2-4 sentences, no bullet points
- chart x and y must be column names from the provided rows
- Revenue means total_fare_amount (excludes tips)
- No markdown, no explanation outside the JSON"""


def _run_summarize(question: str, rows: list[dict], capped: bool, ollama_url: str = OLLAMA_URL) -> dict:
    """Returns {"summary": str, "chart_spec": dict|None}."""
    rows_json = json.dumps(rows[:50], default=str)
    cap_note = " NOTE: results were capped at 200 rows." if capped else ""
    messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {"role": "user", "content": f"Question: {question}{cap_note}\n\nRows:\n{rows_json}"},
    ]
    raw = _ollama_chat(messages, ollama_url=ollama_url)
    parsed = json.loads(raw.strip())
    summary = parsed.get("summary", "").strip()
    chart_spec = parsed.get("chart_spec")

    # Validate chart_spec columns against actual row keys
    if chart_spec and rows:
        col_names = set(rows[0].keys())
        if (chart_spec.get("x") not in col_names or
                chart_spec.get("y") not in col_names or
                chart_spec.get("type") not in {"bar", "line", "pie", "table"}):
            chart_spec = None

    return {"summary": summary, "chart_spec": chart_spec}
```

- [ ] **Step 4: Run all filter tests to confirm nothing is broken**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_intent.py tests/test_filter_pipeline.py -v
```

Expected: all 25 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py
git commit -m "feat: inlined supervisor, query, and summarize agents in filter"
```

---

## Task 5: Open WebUI Filter Class (inlet/outlet/Valves)

**Files:**
- Modify: `openwebui/filter_analytics.py` — add `Valves` dataclass and `Filter` class with `inlet`/`outlet`

- [ ] **Step 1: Add Valves and Filter class**

Append to `openwebui/filter_analytics.py`:

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Valves:
    """Open WebUI admin-configurable settings for this filter."""
    s3_bucket: str = field(default="YOUR_BUCKET_NAME")
    ollama_url: str = field(default="http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions")
    enabled: bool = field(default=True)


class Filter:
    def __init__(self):
        self.valves = Valves()

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Intercept every message before it reaches the LLM."""
        if not self.valves.enabled:
            return body

        # Extract last user message
        messages = body.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return body

        question = user_messages[-1].get("content", "").strip()
        if not question:
            return body

        intent = classify_intent(question)

        if intent == INTENT_CHAT:
            return body

        if intent == INTENT_AMBIGUOUS:
            body["messages"][-1]["content"] = (
                "That sounds data-related — do you want me to run an analytics "
                "query on the NYC taxi dataset? If so, please describe what you'd "
                "like to know (e.g. 'show monthly revenue trend' or 'top boroughs by trips')."
            )
            return body

        # INTENT_ANALYTICS — run pipeline
        # Pass valves values so runtime config overrides module-level constants
        try:
            response_text = _run_analytics(question, self.valves.s3_bucket, self.valves.ollama_url)
        except Exception as e:
            response_text = f"Analytics pipeline error: {e}"

        # Short-circuit: inject response as assistant message, clear pending user message
        body["messages"] = messages[:-1] + [
            {"role": "user", "content": question},
            {"role": "assistant", "content": response_text},
        ]
        # Signal Open WebUI to return the injected assistant message directly
        body["stream"] = False
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body


def _run_analytics(question: str, s3_bucket: str, ollama_url: str = OLLAMA_URL) -> str:
    """Run full supervisor → query → summarize pipeline, return formatted response."""
    # Supervisor
    supervisor = _run_supervisor(question, REGISTRY, ollama_url)

    if supervisor["confidence"] == "low":
        return (
            f"I wasn't confident which data to use for that question. "
            f"Could you be more specific? ({supervisor['reasoning']})"
        )

    table = supervisor["table"]

    # Query
    query_result = _run_query(question, table, REGISTRY, s3_bucket, ollama_url)
    rows = query_result["rows"]
    capped = query_result["capped"]

    if not rows:
        return "No data found for that query."

    # Summarize
    summarize_result = _run_summarize(question, rows, capped, ollama_url)
    summary = summarize_result["summary"]
    chart_spec = summarize_result["chart_spec"]

    parts = [summary]
    if chart_spec:
        html = build_html_artifact(chart_spec, rows)
        if html:
            parts.append(html)

    return "\n\n".join(parts)
```

- [ ] **Step 2: Run all tests**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_intent.py tests/test_filter_pipeline.py -v
```

Expected: all 25 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add openwebui/filter_analytics.py
git commit -m "feat: Filter class with inlet/outlet and Valves config"
```

---

## Task 6: Update S3_BUCKET and OLLAMA_URL Constants

**Files:**
- Modify: `openwebui/filter_analytics.py` — set real S3_BUCKET value and wire Valves to runtime helpers

The `S3_BUCKET` constant in the filter is used as a fallback only. The Valves `s3_bucket` is the runtime value. Make sure `_run_query` uses the passed `s3_bucket` parameter (already does). Also update the `OLLAMA_URL` constant so it matches the cluster-internal address.

- [ ] **Step 1: Verify Ollama cluster URL**

```bash
grep -r "ollama" /media/sirfenrir/Study/LLMOPs/argocd/ --include="*.yaml" | grep "svc\|url\|host" | head -10
```

Expected: should see `http://ollama.ollama.svc.cluster.local:11434` or similar.

- [ ] **Step 2: Update OLLAMA_URL constant in filter**

In `openwebui/filter_analytics.py`, confirm the line reads:

```python
OLLAMA_URL = "http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions"
```

If different, update to match what the grep found.

- [ ] **Step 3: Check S3 bucket name from existing config**

```bash
grep -r "S3_BUCKET\|s3_bucket\|bucket" /media/sirfenrir/Study/LLMOPs/argocd/ --include="*.yaml" | grep -v "#" | head -10
```

Note the bucket name. Update the `Valves` default:

```python
s3_bucket: str = field(default="<actual-bucket-name>")
```

- [ ] **Step 4: Run all tests to confirm nothing broke**

```bash
ANALYTICS_S3_BUCKET=test .venv/bin/pytest tests/test_filter_intent.py tests/test_filter_pipeline.py -v
```

Expected: all 25 PASS.

- [ ] **Step 5: Commit**

```bash
git add openwebui/filter_analytics.py
git commit -m "chore: wire correct Ollama URL and S3 bucket defaults in filter Valves"
```

---

## Task 7: Manual Deployment Verification (Checklist)

No code changes — this is the deployment and smoke test task.

- [ ] **Step 1: Copy filter file content to clipboard**

```bash
cat openwebui/filter_analytics.py | wc -l  # confirm it's reasonable size
```

- [ ] **Step 2: Open Open WebUI admin panel**

Navigate to: `Admin Panel → Functions → Add Function`

Paste the full contents of `openwebui/filter_analytics.py`. Click Save.

- [ ] **Step 3: Enable filter on private-chat model**

Navigate to: `Admin Panel → Models → private-chat → Edit`

Under "Filters", enable the `NYC Taxi Analytics Filter`. Save.

- [ ] **Step 4: Set Valves values**

In the Filter settings, set:
- `s3_bucket`: your actual S3 bucket name
- `ollama_url`: `http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions`
- `enabled`: `true`

- [ ] **Step 5: Test golden analytics question**

In private-chat, send: `show monthly revenue trend`

Expected: analytics pipeline runs, response contains a summary paragraph + an HTML artifact with a line/bar chart rendered inline.

- [ ] **Step 6: Test normal chat passthrough**

In private-chat, send: `explain what a linked list is`

Expected: LLM responds normally, no analytics pipeline triggered.

- [ ] **Step 7: Test ambiguous question**

In private-chat, send: `taxi`

Expected: Filter returns the clarification ask: "That sounds data-related — do you want me to run an analytics query...?"

- [ ] **Step 8: Test all 10 golden questions from schema_registry.json**

Send each of these and verify chart + summary renders:
1. `which hour has the most trips`
2. `revenue by borough`
3. `daily overview`
4. `show monthly revenue trend`
5. `payment type breakdown`
6. `vendor performance`
7. `show weekly trip count`
8. `zone performance by revenue`
9. `show peak hour heatmap`
10. `most popular routes`

- [ ] **Step 9: Commit final state**

```bash
git add openwebui/filter_analytics.py
git commit -m "feat: open-webui filter integration complete — analytics in private-chat"
```
