# NYC Taxi Analytics Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a three-agent (supervisor → query → summarize) natural-language analytics pipeline over NYC taxi Parquet data on S3, with a Streamlit demo UI.

**Architecture:** A Python orchestrator drives three sequential LLM calls via Ollama (OpenAI-compatible API). The supervisor picks the right table from a schema registry JSON, the query agent generates validated SQL, DuckDB executes it against S3 Parquet files, and the summarize agent produces a business summary + Altair chart spec rendered in Streamlit.

**Tech Stack:** Python 3.11+, DuckDB + httpfs, Ollama (`qwen2.5-coder:7b`, `--ctx-size 8192`), boto3, Streamlit, Altair, pytest, python-dotenv

---

## File Structure

```
analytics_agent/
  __init__.py
  config.py              # env vars: S3 bucket, Ollama URL, timeouts
  registry.py            # load + validate schema_registry.json
  agents/
    __init__.py
    supervisor.py        # table selection agent
    query.py             # SQL generation agent + validator
    summarize.py         # summary + chart spec agent
  pipeline.py            # orchestrates the three agents, correlation ID, logging
  ollama_client.py       # thin wrapper: POST /v1/chat/completions, timeout, JSON parse
scripts/
  build_registry.py      # scan local Parquet dirs → write schema_registry.json
  upload_to_s3.py        # aws s3 sync docs/DB/files_list/ + schema_registry.json
app.py                   # Streamlit UI
tests/
  test_registry.py
  test_sql_validator.py
  test_supervisor.py
  test_query_agent.py
  test_summarize_agent.py
  test_pipeline.py
  fixtures/
    schema_registry.json  # minimal fixture (3 tables)
    sample_rows.json       # sample DuckDB result rows
schema_registry.json      # built by scripts/build_registry.py, committed
.env.example              # ANALYTICS_S3_BUCKET, OLLAMA_BASE_URL, AWS_REGION
```

---

## Task 1: Project Scaffold + Config

**Files:**
- Create: `analytics_agent/__init__.py`
- Create: `analytics_agent/config.py`
- Create: `.env.example`
- Create: `requirements.txt`

- [ ] **Step 1: Create requirements.txt**

```
duckdb==1.2.2
boto3==1.38.0
openai==1.82.0
streamlit==1.45.1
altair==5.5.0
python-dotenv==1.1.0
pyarrow==20.0.0
pytest==8.3.5
pytest-mock==3.14.0
```

- [ ] **Step 2: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: no errors.

- [ ] **Step 3: Create `.env.example`**

```bash
ANALYTICS_S3_BUCKET=nyc-taxi-analytics-dev
OLLAMA_BASE_URL=http://localhost:11434
AWS_REGION=ap-southeast-1
OLLAMA_TIMEOUT_SECONDS=60
DUCKDB_TIMEOUT_SECONDS=30
PIPELINE_TIMEOUT_SECONDS=180
ROW_CAP=200
```

- [ ] **Step 4: Create `analytics_agent/config.py`**

```python
import os
from dotenv import load_dotenv

load_dotenv()

S3_BUCKET = os.environ["ANALYTICS_S3_BUCKET"]
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
AWS_REGION = os.getenv("AWS_REGION", "ap-southeast-1")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
DUCKDB_TIMEOUT = int(os.getenv("DUCKDB_TIMEOUT_SECONDS", "30"))
PIPELINE_TIMEOUT = int(os.getenv("PIPELINE_TIMEOUT_SECONDS", "180"))
ROW_CAP = int(os.getenv("ROW_CAP", "200"))
SCHEMA_REGISTRY_PATH = os.getenv("SCHEMA_REGISTRY_PATH", "schema_registry.json")
```

- [ ] **Step 5: Create `analytics_agent/__init__.py`**

```python
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example analytics_agent/
git commit -m "feat: project scaffold and config"
```

---

## Task 2: Schema Registry Builder Script

**Files:**
- Create: `scripts/build_registry.py`
- Create: `schema_registry.json` (output, committed)

- [ ] **Step 1: Write failing test for registry structure**

Create `tests/test_registry.py`:

```python
import json, pytest
from pathlib import Path

FIXTURE = Path("tests/fixtures/schema_registry.json")

def test_registry_has_required_fields():
    registry = json.loads(FIXTURE.read_text())
    for table, entry in registry.items():
        assert "description" in entry, f"{table} missing description"
        assert "tier" in entry, f"{table} missing tier"
        assert "columns" in entry, f"{table} missing columns"
        assert isinstance(entry["columns"], list), f"{table} columns must be list"
        for col in entry["columns"]:
            assert "name" in col, f"{table} column missing name"
            assert "type" in col, f"{table} column missing type"

def test_registry_tier_values():
    registry = json.loads(FIXTURE.read_text())
    valid_tiers = {"fact", "dim", "kpi", "route", "ops", "dq"}
    for table, entry in registry.items():
        assert entry["tier"] in valid_tiers, f"{table} has invalid tier: {entry['tier']}"
```

- [ ] **Step 2: Create fixture `tests/fixtures/schema_registry.json`**

```json
{
  "kpi_monthly_summary": {
    "description": "Monthly aggregated revenue, trips, and AOV across all zones",
    "tier": "kpi",
    "columns": [
      {"name": "month", "type": "date"},
      {"name": "revenue", "type": "double"},
      {"name": "trip_count", "type": "int64"},
      {"name": "avg_fare", "type": "double"}
    ],
    "example_questions": ["show monthly revenue trend", "which month had the most trips"]
  },
  "fact_trips_daily": {
    "description": "Daily trip grain with fare, distance, and zone identifiers",
    "tier": "fact",
    "columns": [
      {"name": "trip_date", "type": "date"},
      {"name": "vendor_id", "type": "int32"},
      {"name": "pickup_zone_id", "type": "int32"},
      {"name": "dropoff_zone_id", "type": "int32"},
      {"name": "trip_count", "type": "int64"},
      {"name": "total_fare", "type": "double"},
      {"name": "avg_distance", "type": "double"}
    ],
    "example_questions": ["daily trips last month", "fare by pickup zone"]
  },
  "dim_zone": {
    "description": "Zone dimension with borough and service zone labels",
    "tier": "dim",
    "columns": [
      {"name": "zone_id", "type": "int32"},
      {"name": "zone_name", "type": "varchar"},
      {"name": "borough", "type": "varchar"},
      {"name": "service_zone", "type": "varchar"}
    ],
    "example_questions": ["list all boroughs", "zones in Manhattan"]
  }
}
```

- [ ] **Step 3: Run test to verify it passes on fixture**

```bash
pytest tests/test_registry.py -v
```

Expected: PASS (fixture already satisfies structure).

- [ ] **Step 4: Write `scripts/build_registry.py`**

```python
#!/usr/bin/env python3
"""Scan local Parquet dirs and write schema_registry.json.

Usage: python scripts/build_registry.py --source docs/DB/files_list --output schema_registry.json
"""
import argparse, json
from pathlib import Path
import pyarrow.parquet as pq

TIER_MAP = {
    "fact_": "fact", "dim_": "dim", "kpi_": "kpi",
    "route_": "route", "ops_": "ops", "dq_": "dq",
}

def infer_tier(table_name: str) -> str:
    for prefix, tier in TIER_MAP.items():
        if table_name.startswith(prefix):
            return tier
    return "fact"

def scan_table(table_dir: Path) -> dict:
    parquet_files = list(table_dir.glob("*.parquet"))
    if not parquet_files:
        raise ValueError(f"No parquet files in {table_dir}")
    schema = pq.read_schema(parquet_files[0])
    return {
        "description": f"{table_dir.name.replace('_', ' ').title()} — auto-generated, update manually",
        "tier": infer_tier(table_dir.name),
        "columns": [
            {"name": schema.field(i).name, "type": str(schema.field(i).type)}
            for i in range(len(schema))
        ],
        "example_questions": [],
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="docs/DB/files_list")
    parser.add_argument("--output", default="schema_registry.json")
    args = parser.parse_args()

    source = Path(args.source)
    registry = {}
    for table_dir in sorted(source.iterdir()):
        if table_dir.is_dir():
            try:
                registry[table_dir.name] = scan_table(table_dir)
                print(f"  OK  {table_dir.name}")
            except Exception as e:
                print(f"  ERR {table_dir.name}: {e}")

    Path(args.output).write_text(json.dumps(registry, indent=2))
    print(f"\nWrote {len(registry)} tables to {args.output}")

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run builder and verify output**

```bash
python scripts/build_registry.py --source docs/DB/files_list --output schema_registry.json
```

Expected: prints `OK <table>` for all 32 tables, writes `schema_registry.json`.

```bash
python -c "import json; r=json.load(open('schema_registry.json')); print(len(r), 'tables')"
```

Expected: `32 tables`

- [ ] **Step 6: Manually add descriptions and example_questions**

Open `schema_registry.json` and replace auto-generated descriptions for the 10 golden question tables:

```json
"kpi_monthly_summary": {
  "description": "Monthly aggregated revenue, trips, and AOV across all zones",
  "example_questions": ["show monthly revenue trend", "which month had the most trips"]
},
"fact_trips_hourly": {
  "description": "Hourly trip counts and fares aggregated across all zones",
  "example_questions": ["which hour has the most trips", "peak hour revenue"]
},
"kpi_weekly_trends": {
  "description": "Weekly revenue, trip count, and AOV trends",
  "example_questions": ["show weekly trip count", "weekly revenue trend"]
},
"kpi_borough_comparison": {
  "description": "Revenue and trip counts broken down by NYC borough",
  "example_questions": ["revenue by borough", "which borough has most trips"]
},
"route_popular_routes": {
  "description": "Most frequent pickup-to-dropoff zone pairs by trip count",
  "example_questions": ["most popular routes", "top pickup to dropoff zones"]
},
"kpi_zone_performance": {
  "description": "Revenue, trips, and AOV per zone",
  "example_questions": ["zone performance by revenue", "top zones by fare"]
},
"ops_peak_hours_heatmap": {
  "description": "Trip count by hour-of-day and day-of-week for heatmap display",
  "example_questions": ["show peak hour heatmap", "busy hours by day"]
},
"kpi_payment_trends": {
  "description": "Payment type breakdown (cash, card, etc.) by period",
  "example_questions": ["payment type breakdown", "how do passengers pay"]
},
"kpi_daily_overview": {
  "description": "Daily revenue, trips, and AOV for recent days",
  "example_questions": ["daily overview", "recent days summary"]
},
"kpi_vendor_performance": {
  "description": "Trip count and revenue by taxi vendor",
  "example_questions": ["vendor performance", "which vendor has most trips"]
}
```

- [ ] **Step 7: Commit**

```bash
git add scripts/build_registry.py schema_registry.json tests/test_registry.py tests/fixtures/schema_registry.json
git commit -m "feat: schema registry builder script and fixture"
```

---

## Task 3: Registry Loader + Startup Health Check

**Files:**
- Create: `analytics_agent/registry.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_registry.py`:

```python
import pytest
from unittest.mock import patch, mock_open
from analytics_agent.registry import load_registry, validate_registry

def test_load_registry_parses_json():
    fixture = Path("tests/fixtures/schema_registry.json").read_text()
    with patch("builtins.open", mock_open(read_data=fixture)):
        registry = load_registry("tests/fixtures/schema_registry.json")
    assert "kpi_monthly_summary" in registry

def test_load_registry_raises_on_bad_json():
    with patch("builtins.open", mock_open(read_data="{bad json")):
        with pytest.raises(ValueError, match="Failed to parse"):
            load_registry("schema_registry.json")

def test_validate_registry_passes_fixture():
    registry = json.loads(Path("tests/fixtures/schema_registry.json").read_text())
    validate_registry(registry)  # should not raise

def test_validate_registry_raises_on_missing_description():
    registry = {"bad_table": {"tier": "kpi", "columns": []}}
    with pytest.raises(ValueError, match="bad_table missing description"):
        validate_registry(registry)

def test_get_table_schema_returns_slice():
    from analytics_agent.registry import get_table_schema
    registry = json.loads(Path("tests/fixtures/schema_registry.json").read_text())
    schema = get_table_schema(registry, "kpi_monthly_summary")
    assert schema["columns"][0]["name"] == "month"

def test_get_table_schema_raises_on_unknown():
    from analytics_agent.registry import get_table_schema
    with pytest.raises(KeyError, match="unknown_table"):
        get_table_schema({}, "unknown_table")
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_registry.py -v
```

Expected: FAIL — `analytics_agent.registry` not found.

- [ ] **Step 3: Implement `analytics_agent/registry.py`**

```python
import json
from pathlib import Path

def load_registry(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse schema registry at {path}: {e}")

def validate_registry(registry: dict) -> None:
    required = {"description", "tier", "columns"}
    valid_tiers = {"fact", "dim", "kpi", "route", "ops", "dq"}
    for table, entry in registry.items():
        missing = required - entry.keys()
        if missing:
            raise ValueError(f"{table} missing {missing}")
        if entry["tier"] not in valid_tiers:
            raise ValueError(f"{table} has invalid tier: {entry['tier']}")

def get_table_schema(registry: dict, table: str) -> dict:
    if table not in registry:
        raise KeyError(f"unknown_table: {table} not in registry")
    return registry[table]

def registry_as_prompt_text(registry: dict) -> str:
    lines = []
    for table, entry in registry.items():
        col_list = ", ".join(f"{c['name']}({c['type']})" for c in entry["columns"])
        examples = "; ".join(entry.get("example_questions", []))
        lines.append(f"- {table} [{entry['tier']}]: {entry['description']} | columns: {col_list} | examples: {examples}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_registry.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics_agent/registry.py tests/test_registry.py
git commit -m "feat: registry loader, validator, and schema slice helper"
```

---

## Task 4: Ollama Client

**Files:**
- Create: `analytics_agent/ollama_client.py`
- Create: `analytics_agent/agents/__init__.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_ollama_client.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from analytics_agent.ollama_client import chat, OllamaError

def _mock_response(content: str):
    mock = MagicMock()
    mock.choices[0].message.content = content
    return mock

def test_chat_returns_content(monkeypatch):
    with patch("analytics_agent.ollama_client._client") as mock_client:
        mock_client.chat.completions.create.return_value = _mock_response("hello")
        result = chat(messages=[{"role": "user", "content": "hi"}])
    assert result == "hello"

def test_chat_raises_ollama_error_on_timeout(monkeypatch):
    import openai
    with patch("analytics_agent.ollama_client._client") as mock_client:
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(request=MagicMock())
        with pytest.raises(OllamaError, match="timed out"):
            chat(messages=[{"role": "user", "content": "hi"}])

def test_chat_strips_markdown_fences():
    from analytics_agent.ollama_client import strip_fences
    raw = "```sql\nSELECT * FROM t\n```"
    assert strip_fences(raw) == "SELECT * FROM t"

def test_strip_fences_passthrough_plain():
    from analytics_agent.ollama_client import strip_fences
    assert strip_fences("SELECT 1") == "SELECT 1"
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_ollama_client.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `analytics_agent/ollama_client.py`**

```python
import re
import openai
from analytics_agent.config import OLLAMA_BASE_URL, OLLAMA_TIMEOUT

class OllamaError(Exception):
    pass

_client = openai.OpenAI(
    base_url=f"{OLLAMA_BASE_URL}/v1",
    api_key="ollama",
)

MODEL = "qwen2.5-coder:7b"

def chat(messages: list[dict], model: str = MODEL) -> str:
    try:
        response = _client.chat.completions.create(
            model=model,
            messages=messages,
            timeout=OLLAMA_TIMEOUT,
        )
        return response.choices[0].message.content
    except openai.APITimeoutError:
        raise OllamaError(f"Ollama timed out after {OLLAMA_TIMEOUT}s")
    except openai.APIConnectionError as e:
        raise OllamaError(f"Ollama connection failed: {e}")

def strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:sql)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    return match.group(1).strip() if match else text
```

- [ ] **Step 4: Create `analytics_agent/agents/__init__.py`**

```python
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_ollama_client.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add analytics_agent/ollama_client.py analytics_agent/agents/__init__.py tests/test_ollama_client.py
git commit -m "feat: ollama client with timeout and fence stripping"
```

---

## Task 5: SQL Validator

**Files:**
- Create: `analytics_agent/agents/query.py` (validator only first)

- [ ] **Step 1: Write failing tests**

Create `tests/test_sql_validator.py`:

```python
import pytest
from analytics_agent.agents.query import validate_sql, SQLValidationError

KNOWN_TABLES = {"kpi_monthly_summary", "fact_trips_daily", "dim_zone"}

def test_valid_select_passes():
    validate_sql("SELECT revenue FROM kpi_monthly_summary WHERE month = '2026-01-01'", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_non_select():
    with pytest.raises(SQLValidationError, match="must start with SELECT"):
        validate_sql("DROP TABLE kpi_monthly_summary", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_ddl_in_select():
    with pytest.raises(SQLValidationError, match="DDL"):
        validate_sql("SELECT * FROM kpi_monthly_summary; DROP TABLE kpi_monthly_summary", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_chained_statements():
    with pytest.raises(SQLValidationError, match="chained"):
        validate_sql("SELECT 1; SELECT 2", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_wrong_table():
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate_sql("SELECT * FROM fact_trips_daily", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_read_parquet():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql("SELECT * FROM read_parquet('s3://evil/path')", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_httpfs():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql("SELECT * FROM read_csv_auto('http://evil.com/data.csv')", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_copy():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql("COPY (SELECT * FROM kpi_monthly_summary) TO '/tmp/out.csv'", "kpi_monthly_summary", KNOWN_TABLES)

def test_rejects_unknown_table_reference():
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate_sql("SELECT * FROM secret_table", "kpi_monthly_summary", KNOWN_TABLES)
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_sql_validator.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement validator in `analytics_agent/agents/query.py`**

```python
import re
import duckdb
from analytics_agent.config import DUCKDB_TIMEOUT, ROW_CAP, S3_BUCKET

DDL_KEYWORDS = re.compile(
    r'\b(DROP|CREATE|INSERT|UPDATE|DELETE|ALTER|TRUNCATE)\b', re.IGNORECASE
)
FILE_FUNCTIONS = re.compile(
    r'\b(read_parquet|read_csv_auto|read_json|COPY|EXPORT|httpfs)\b', re.IGNORECASE
)

class SQLValidationError(Exception):
    pass

def validate_sql(sql: str, expected_table: str, known_tables: set[str]) -> None:
    stripped = sql.strip()

    if not stripped.upper().startswith("SELECT"):
        raise SQLValidationError("SQL must start with SELECT")

    if ";" in stripped:
        raise SQLValidationError("chained statements not allowed (semicolon found)")

    if DDL_KEYWORDS.search(stripped):
        raise SQLValidationError("DDL keywords not allowed")

    if FILE_FUNCTIONS.search(stripped):
        raise SQLValidationError("file function not allowed (read_parquet, httpfs, COPY, etc.)")

    # check only the expected table appears (and no other known or unknown tables)
    found_tables = set(re.findall(r'\bFROM\s+(\w+)', stripped, re.IGNORECASE))
    found_tables |= set(re.findall(r'\bJOIN\s+(\w+)', stripped, re.IGNORECASE))
    for t in found_tables:
        if t.lower() != expected_table.lower():
            raise SQLValidationError(f"Table '{t}' not allowed — expected '{expected_table}'")
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_sql_validator.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics_agent/agents/query.py tests/test_sql_validator.py
git commit -m "feat: SQL validator with DDL, file-function, and table whitelist checks"
```

---

## Task 6: Supervisor Agent

**Files:**
- Create: `analytics_agent/agents/supervisor.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_supervisor.py`:

```python
import json, pytest
from unittest.mock import patch
from pathlib import Path
from analytics_agent.agents.supervisor import run_supervisor, SupervisorResult, SupervisorError

REGISTRY = json.loads(Path("tests/fixtures/schema_registry.json").read_text())

def _mock_chat(content: str):
    return lambda messages, **_: content

def test_supervisor_returns_high_confidence():
    output = json.dumps({"table": "kpi_monthly_summary", "confidence": "high", "reasoning": "monthly revenue"})
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        result = run_supervisor("show monthly revenue trend", REGISTRY)
    assert result.table == "kpi_monthly_summary"
    assert result.confidence == "high"

def test_supervisor_returns_low_confidence():
    output = json.dumps({"table": "dim_zone", "confidence": "low", "reasoning": "ambiguous"})
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        result = run_supervisor("show something", REGISTRY)
    assert result.confidence == "low"

def test_supervisor_treats_unknown_confidence_as_low():
    output = json.dumps({"table": "kpi_monthly_summary", "confidence": "medium", "reasoning": "..."})
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        result = run_supervisor("something", REGISTRY)
    assert result.confidence == "low"
    assert result.unexpected_confidence == "medium"

def test_supervisor_raises_on_unknown_table():
    output = json.dumps({"table": "nonexistent_table", "confidence": "high", "reasoning": "..."})
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat(output)):
        with pytest.raises(SupervisorError, match="not in registry"):
            run_supervisor("something", REGISTRY)

def test_supervisor_raises_on_invalid_json():
    with patch("analytics_agent.agents.supervisor.chat", _mock_chat("not json")):
        with pytest.raises(SupervisorError, match="parse"):
            run_supervisor("something", REGISTRY)
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_supervisor.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `analytics_agent/agents/supervisor.py`**

```python
import json
from dataclasses import dataclass, field
from analytics_agent.ollama_client import chat, OllamaError
from analytics_agent.registry import registry_as_prompt_text

SYSTEM_PROMPT = """You are a table selection agent for NYC yellow cab trip analytics.

Dataset tiers:
- kpi: pre-aggregated monthly/weekly/daily metrics — prefer these for summary questions
- fact: daily/hourly grain with zone and vendor IDs — use for detailed filtering
- dim: lookup tables (zone names, boroughs, vendors)
- route: pickup-to-dropoff zone pair aggregates
- ops: operational patterns (peak hours, passenger counts, distances)
- dq: data quality checks — only if asked about data quality

Borough names in this dataset: Manhattan, Brooklyn, Queens, Bronx, Staten Island.
Revenue = total_fare_amount (excludes tips).
Peak hours = 7-9am and 5-8pm.

Select ONE table. Output ONLY valid JSON, no explanation, no markdown:
{"table": "<table_name>", "confidence": "high|low", "reasoning": "<one sentence>"}

You MUST output exactly "high" or "low" for confidence. No other values."""

@dataclass
class SupervisorResult:
    table: str
    confidence: str
    reasoning: str
    raw_response: str
    unexpected_confidence: str = ""

class SupervisorError(Exception):
    pass

def run_supervisor(question: str, registry: dict) -> SupervisorResult:
    registry_text = registry_as_prompt_text(registry)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Available tables:\n{registry_text}\n\nQuestion: {question}"},
    ]
    raw = chat(messages=messages)

    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise SupervisorError(f"Failed to parse supervisor output: {e} | raw: {raw}")

    table = parsed.get("table", "")
    if table not in registry:
        raise SupervisorError(f"Supervisor selected '{table}' which is not in registry")

    confidence = parsed.get("confidence", "low")
    unexpected = ""
    if confidence not in ("high", "low"):
        unexpected = confidence
        confidence = "low"

    return SupervisorResult(
        table=table,
        confidence=confidence,
        reasoning=parsed.get("reasoning", ""),
        raw_response=raw,
        unexpected_confidence=unexpected,
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_supervisor.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics_agent/agents/supervisor.py tests/test_supervisor.py
git commit -m "feat: supervisor agent with table selection and confidence validation"
```

---

## Task 7: Query Agent + DuckDB Execution

**Files:**
- Modify: `analytics_agent/agents/query.py` (add query agent + DuckDB runner)
- Create: `tests/fixtures/sample_rows.json`

- [ ] **Step 1: Write failing tests**

Create `tests/test_query_agent.py`:

```python
import json, pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from analytics_agent.agents.query import run_query_agent, QueryResult, QueryError

REGISTRY = json.loads(Path("tests/fixtures/schema_registry.json").read_text())
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_query_agent.py -v
```

Expected: FAIL.

- [ ] **Step 3: Add query agent + DuckDB runner to `analytics_agent/agents/query.py`**

Append to the existing file (keep validator code, add below):

```python
import signal
from dataclasses import dataclass
from analytics_agent.ollama_client import chat, strip_fences
from analytics_agent.registry import get_table_schema, registry_as_prompt_text
from analytics_agent.config import S3_BUCKET, DUCKDB_TIMEOUT, ROW_CAP

QUERY_SYSTEM_PROMPT = """You are a SQL query agent for NYC yellow cab trip analytics stored in Parquet files on S3.

Rules:
- Write ONE SELECT statement only
- No markdown, no explanation, just the SQL
- Use only the table and columns provided
- UTC timestamps — do not convert timezone
- Revenue = total_fare_amount (excludes tips)
- Borough values: Manhattan, Brooklyn, Queens, Bronx, Staten Island
- Peak hours: 7-9 and 17-20 (24h)
- Do not use read_parquet(), httpfs, or any file functions"""

@dataclass
class QueryResult:
    sql: str
    rows: list[dict]
    capped: bool
    row_count_before_cap: int

class QueryError(Exception):
    pass

def _execute_duckdb(sql: str, table: str) -> list[dict]:
    import duckdb

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"DuckDB query exceeded {DUCKDB_TIMEOUT}s")

    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(DUCKDB_TIMEOUT)
    try:
        path = f"s3://{S3_BUCKET}/{table}/*.parquet"
        conn = duckdb.connect()
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        conn.execute("SET s3_region='ap-southeast-1';")
        # Use instance profile credentials (no static keys)
        conn.execute("SET s3_use_credential_chain=true;")
        view_sql = sql.replace(table, f"read_parquet('{path}')")
        result = conn.execute(view_sql).fetchdf()
        return result.to_dict(orient="records")
    finally:
        signal.alarm(0)

def run_query_agent(
    question: str,
    table: str,
    registry: dict,
    known_tables: set[str],
) -> QueryResult:
    schema = get_table_schema(registry, table)
    col_text = ", ".join(f"{c['name']} ({c['type']})" for c in schema["columns"])
    messages = [
        {"role": "system", "content": QUERY_SYSTEM_PROMPT},
        {"role": "user", "content": f"Table: {table}\nColumns: {col_text}\n\nQuestion: {question}"},
    ]
    raw = chat(messages=messages)
    sql = strip_fences(raw)

    try:
        validate_sql(sql, table, known_tables)
    except SQLValidationError as e:
        raise QueryError(f"SQL validation failed: {e} | sql: {sql}")

    try:
        rows = _execute_duckdb(sql, table)
    except TimeoutError as e:
        raise QueryError(str(e))
    except Exception as e:
        raise QueryError(f"DuckDB error: {e}")

    before_cap = len(rows)
    capped = before_cap > ROW_CAP
    return QueryResult(sql=sql, rows=rows[:ROW_CAP], capped=capped, row_count_before_cap=before_cap)
```

- [ ] **Step 4: Create `tests/fixtures/sample_rows.json`**

```json
[
  {"month": "2026-01-01", "revenue": 1234567.89, "trip_count": 98432, "avg_fare": 12.54},
  {"month": "2026-02-01", "revenue": 1189432.10, "trip_count": 94201, "avg_fare": 12.63},
  {"month": "2026-03-01", "revenue": 1312000.00, "trip_count": 102100, "avg_fare": 12.85}
]
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_query_agent.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add analytics_agent/agents/query.py tests/test_query_agent.py tests/fixtures/sample_rows.json
git commit -m "feat: query agent with SQL generation, validation, and DuckDB execution"
```

---

## Task 8: Summarize Agent

**Files:**
- Create: `analytics_agent/agents/summarize.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_summarize_agent.py`:

```python
import json, pytest
from unittest.mock import patch
from pathlib import Path
from analytics_agent.agents.summarize import run_summarize_agent, SummarizeResult, SummarizeError

ROWS = json.loads(Path("tests/fixtures/sample_rows.json").read_text())

def _mock_chat(content: str):
    return lambda messages, **_: content

def test_summarize_returns_summary_and_chart():
    output = json.dumps({
        "summary": "January was the peak revenue month.",
        "chart_spec": {"type": "bar", "x": "month", "y": "revenue", "series": []},
        "capped": False
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show monthly revenue", ROWS, capped=False)
    assert "January" in result.summary
    assert result.chart_spec["type"] == "bar"

def test_summarize_notes_cap_in_output():
    output = json.dumps({
        "summary": "Results limited to 200 rows.",
        "chart_spec": {"type": "table", "x": "month", "y": "revenue", "series": []},
        "capped": True
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
        "capped": False
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        with pytest.raises(SummarizeError, match="empty summary"):
            run_summarize_agent("show revenue", ROWS, capped=False)

def test_summarize_drops_chart_on_missing_column():
    output = json.dumps({
        "summary": "Good summary.",
        "chart_spec": {"type": "bar", "x": "nonexistent_col", "y": "revenue", "series": []},
        "capped": False
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show revenue", ROWS, capped=False)
    assert result.chart_spec is None
    assert result.chart_invalid_reason is not None

def test_summarize_rejects_invalid_chart_type():
    output = json.dumps({
        "summary": "Good summary.",
        "chart_spec": {"type": "scatter", "x": "month", "y": "revenue", "series": []},
        "capped": False
    })
    with patch("analytics_agent.agents.summarize.chat", _mock_chat(output)):
        result = run_summarize_agent("show revenue", ROWS, capped=False)
    assert result.chart_spec is None
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_summarize_agent.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `analytics_agent/agents/summarize.py`**

```python
import json
from dataclasses import dataclass
from analytics_agent.ollama_client import chat, OllamaError

VALID_CHART_TYPES = {"bar", "line", "pie", "table"}

SYSTEM_PROMPT = """You are a business analytics summarizer for NYC yellow cab trip data.
Your audience is operations and business users. Write in plain English.

Given a question and query result rows, output ONLY valid JSON:
{
  "summary": "<2-4 sentence business summary>",
  "chart_spec": {"type": "bar|line|pie|table", "x": "<column>", "y": "<column>", "series": []},
  "capped": <true if rows were capped, else false>
}

Rules:
- summary must be 2-4 sentences, no bullet points
- chart x and y must be column names from the provided rows
- if rows were capped at 200, note it in the summary
- Revenue means total_fare_amount (excludes tips)
- No markdown, no explanation outside the JSON"""

@dataclass
class SummarizeResult:
    summary: str
    chart_spec: dict | None
    capped: bool
    chart_invalid_reason: str = ""
    raw_response: str = ""

class SummarizeError(Exception):
    pass

def _validate_chart_spec(chart_spec: dict, rows: list[dict]) -> str:
    if not rows:
        return "no rows to chart"
    if chart_spec.get("type") not in VALID_CHART_TYPES:
        return f"invalid chart type: {chart_spec.get('type')}"
    col_names = set(rows[0].keys())
    if chart_spec.get("x") not in col_names:
        return f"x column '{chart_spec.get('x')}' not in rows"
    if chart_spec.get("y") not in col_names:
        return f"y column '{chart_spec.get('y')}' not in rows"
    return ""

def run_summarize_agent(question: str, rows: list[dict], capped: bool) -> SummarizeResult:
    rows_json = json.dumps(rows[:50], default=str)
    cap_note = " NOTE: results were capped at 200 rows." if capped else ""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Question: {question}{cap_note}\n\nRows:\n{rows_json}"},
    ]
    raw = chat(messages=messages)

    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise SummarizeError(f"Failed to parse summarize output: {e} | raw: {raw}")

    summary = parsed.get("summary", "").strip()
    if not summary:
        raise SummarizeError("empty summary returned by model")

    chart_spec = parsed.get("chart_spec")
    chart_invalid = ""
    if chart_spec:
        chart_invalid = _validate_chart_spec(chart_spec, rows)
        if chart_invalid:
            chart_spec = None

    return SummarizeResult(
        summary=summary,
        chart_spec=chart_spec,
        capped=parsed.get("capped", capped),
        chart_invalid_reason=chart_invalid,
        raw_response=raw,
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_summarize_agent.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add analytics_agent/agents/summarize.py tests/test_summarize_agent.py
git commit -m "feat: summarize agent with chart spec validation"
```

---

## Task 9: Pipeline Orchestrator + Structured Logging

**Files:**
- Create: `analytics_agent/pipeline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline.py`:

```python
import json, pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from analytics_agent.pipeline import run_pipeline, PipelineResult

REGISTRY = json.loads(Path("tests/fixtures/schema_registry.json").read_text())

def _make_supervisor(table="kpi_monthly_summary", confidence="high"):
    from analytics_agent.agents.supervisor import SupervisorResult
    return SupervisorResult(table=table, confidence=confidence, reasoning="ok", raw_response="{}")

def _make_query(rows=None):
    from analytics_agent.agents.query import QueryResult
    return QueryResult(sql="SELECT 1", rows=rows or [{"month": "2026-01", "revenue": 100.0}], capped=False, row_count_before_cap=1)

def _make_summarize():
    from analytics_agent.agents.summarize import SummarizeResult
    return SummarizeResult(summary="Good summary.", chart_spec={"type": "bar", "x": "month", "y": "revenue", "series": []}, capped=False)

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

def test_pipeline_returns_error_on_empty_rows():
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
```

- [ ] **Step 2: Run to verify they fail**

```bash
pytest tests/test_pipeline.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `analytics_agent/pipeline.py`**

```python
import json
import uuid
import time
import logging
from dataclasses import dataclass, field

from analytics_agent.agents.supervisor import run_supervisor, SupervisorError
from analytics_agent.agents.query import run_query_agent, QueryError
from analytics_agent.agents.summarize import run_summarize_agent, SummarizeError
from analytics_agent.ollama_client import OllamaError

logger = logging.getLogger(__name__)

@dataclass
class PipelineResult:
    correlation_id: str
    summary: str | None = None
    chart_spec: dict | None = None
    clarification: str | None = None
    error: str | None = None
    log: dict = field(default_factory=dict)

def run_pipeline(question: str, registry: dict) -> PipelineResult:
    correlation_id = str(uuid.uuid4())
    log: dict = {"correlation_id": correlation_id, "question": question}
    t0 = time.monotonic()

    try:
        # --- Supervisor ---
        t1 = time.monotonic()
        supervisor_result = run_supervisor(question, registry)
        log["supervisor"] = {
            "table_selected": supervisor_result.table,
            "confidence": supervisor_result.confidence,
            "reasoning": supervisor_result.reasoning,
            "unexpected_confidence": supervisor_result.unexpected_confidence,
            "latency_ms": int((time.monotonic() - t1) * 1000),
            "raw_response": supervisor_result.raw_response,
        }

        if supervisor_result.confidence == "low":
            log["outcome"] = "clarification"
            _emit_log(log)
            return PipelineResult(
                correlation_id=correlation_id,
                clarification=f"Could you clarify your question? I wasn't confident which data to use. Reasoning: {supervisor_result.reasoning}",
                log=log,
            )

        # --- Query ---
        t2 = time.monotonic()
        known_tables = set(registry.keys())
        query_result = run_query_agent(question, supervisor_result.table, registry, known_tables)
        log["query"] = {
            "sql": query_result.sql,
            "validator_passed": True,
            "row_count": len(query_result.rows),
            "row_count_before_cap": query_result.row_count_before_cap,
            "capped": query_result.capped,
            "latency_ms": int((time.monotonic() - t2) * 1000),
        }

        if not query_result.rows:
            log["outcome"] = "empty_rows"
            _emit_log(log)
            return PipelineResult(
                correlation_id=correlation_id,
                summary="No data found for this period.",
                log=log,
            )

        # --- Summarize ---
        t3 = time.monotonic()
        summarize_result = run_summarize_agent(question, query_result.rows, query_result.capped)
        log["summarize"] = {
            "chart_spec_valid": summarize_result.chart_spec is not None,
            "chart_invalid_reason": summarize_result.chart_invalid_reason,
            "capped": summarize_result.capped,
            "latency_ms": int((time.monotonic() - t3) * 1000),
            "raw_response": summarize_result.raw_response,
        }

        log["total_latency_ms"] = int((time.monotonic() - t0) * 1000)
        log["outcome"] = "success"
        _emit_log(log)

        return PipelineResult(
            correlation_id=correlation_id,
            summary=summarize_result.summary,
            chart_spec=summarize_result.chart_spec,
            log=log,
        )

    except (SupervisorError, QueryError, SummarizeError, OllamaError) as e:
        log["error"] = str(e)
        log["total_latency_ms"] = int((time.monotonic() - t0) * 1000)
        log["outcome"] = "error"
        _emit_log(log)
        return PipelineResult(correlation_id=correlation_id, error=str(e), log=log)

def _emit_log(log: dict) -> None:
    logger.info(json.dumps(log, default=str))
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_pipeline.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all PASS across all test files.

- [ ] **Step 6: Commit**

```bash
git add analytics_agent/pipeline.py tests/test_pipeline.py
git commit -m "feat: pipeline orchestrator with structured logging and correlation ID"
```

---

## Task 10: S3 Upload Script

**Files:**
- Create: `scripts/upload_to_s3.py`

- [ ] **Step 1: Write `scripts/upload_to_s3.py`**

```python
#!/usr/bin/env python3
"""Upload Parquet files and schema_registry.json to S3.

Usage: python scripts/upload_to_s3.py --bucket nyc-taxi-analytics-dev --source docs/DB/files_list
"""
import argparse, boto3, os
from pathlib import Path

def upload(bucket: str, source: Path, dry_run: bool = False) -> None:
    s3 = boto3.client("s3")

    # Upload schema registry
    registry = Path("schema_registry.json")
    if not registry.exists():
        raise FileNotFoundError("schema_registry.json not found — run scripts/build_registry.py first")
    if dry_run:
        print(f"  DRY  schema_registry.json → s3://{bucket}/schema_registry.json")
    else:
        s3.upload_file(str(registry), bucket, "schema_registry.json")
        print(f"  OK   schema_registry.json → s3://{bucket}/schema_registry.json")

    # Upload parquet files preserving directory structure
    for parquet_file in sorted(source.rglob("*.parquet")):
        key = str(parquet_file.relative_to(source))
        if dry_run:
            print(f"  DRY  {key}")
        else:
            s3.upload_file(str(parquet_file), bucket, key)
            print(f"  OK   {key}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--source", default="docs/DB/files_list")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    upload(args.bucket, Path(args.source), dry_run=args.dry_run)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run to verify file list**

```bash
python scripts/upload_to_s3.py --bucket nyc-taxi-analytics-dev --source docs/DB/files_list --dry-run
```

Expected: prints all 43 parquet files + schema_registry.json with `DRY` prefix.

- [ ] **Step 3: Create S3 bucket (run once)**

```bash
aws s3 mb s3://nyc-taxi-analytics-dev --region ap-southeast-1
aws s3api put-public-access-block \
  --bucket nyc-taxi-analytics-dev \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
```

- [ ] **Step 4: Upload**

```bash
python scripts/upload_to_s3.py --bucket nyc-taxi-analytics-dev --source docs/DB/files_list
```

Expected: prints `OK` for all 44 files.

- [ ] **Step 5: Verify**

```bash
aws s3 ls s3://nyc-taxi-analytics-dev/ --recursive | wc -l
```

Expected: 44 (43 parquet + 1 schema registry).

- [ ] **Step 6: Commit**

```bash
git add scripts/upload_to_s3.py
git commit -m "feat: S3 upload script with dry-run support"
```

---

## Task 11: EC2 + Ollama Setup

**Files:**
- Create: `scripts/setup_ollama.sh`

- [ ] **Step 1: Launch EC2 instance**

```bash
aws ec2 run-instances \
  --image-id ami-0c55b159cbfafe1f0 \
  --instance-type g4dn.xlarge \
  --key-name your-key-name \
  --iam-instance-profile Name=nyc-taxi-analytics-ec2-role \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=nyc-taxi-ollama}]' \
  --region ap-southeast-1
```

Replace `your-key-name` with your EC2 key pair name. Replace AMI with the latest Ubuntu 22.04 GPU AMI for your region.

- [ ] **Step 2: Create `scripts/setup_ollama.sh`**

```bash
#!/bin/bash
set -e

# Install NVIDIA drivers + CUDA (Ubuntu 22.04)
apt-get update -y
apt-get install -y ubuntu-drivers-common
ubuntu-drivers install --gpgpu nvidia:535

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Configure Ollama context size
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf << EOF
[Service]
Environment="OLLAMA_NUM_CTX=8192"
EOF

systemctl daemon-reload
systemctl enable ollama
systemctl start ollama

# Pull model
ollama pull qwen2.5-coder:7b

echo "Ollama ready. Test: curl http://localhost:11434/api/tags"
```

- [ ] **Step 3: Run setup on EC2**

SSH into the instance and run:

```bash
scp scripts/setup_ollama.sh ubuntu@<EC2_IP>:~/
ssh ubuntu@<EC2_IP> "sudo bash setup_ollama.sh"
```

Expected: ends with `Ollama ready.`

- [ ] **Step 4: Verify Ollama is running**

```bash
ssh ubuntu@<EC2_IP> "curl -s http://localhost:11434/api/tags | python3 -m json.tool"
```

Expected: JSON with `qwen2.5-coder:7b` in models list.

- [ ] **Step 5: Set OLLAMA_BASE_URL in your `.env`**

```bash
OLLAMA_BASE_URL=http://<EC2_IP>:11434
```

- [ ] **Step 6: Commit**

```bash
git add scripts/setup_ollama.sh
git commit -m "feat: EC2 Ollama setup script with ctx-size 8192"
```

---

## Task 12: IAM Role for EC2

**Files:**
- Create: `scripts/create_iam_role.sh`

- [ ] **Step 1: Create `scripts/create_iam_role.sh`**

```bash
#!/bin/bash
set -e
BUCKET="${1:-nyc-taxi-analytics-dev}"
ROLE_NAME="nyc-taxi-analytics-ec2-role"

# Trust policy
cat > /tmp/trust.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
EOF

# S3 read-only policy for the specific bucket
cat > /tmp/policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::${BUCKET}/*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::${BUCKET}"
    }
  ]
}
EOF

aws iam create-role --role-name $ROLE_NAME --assume-role-policy-document file:///tmp/trust.json
aws iam put-role-policy --role-name $ROLE_NAME --policy-name S3ReadOnly --policy-document file:///tmp/policy.json
aws iam create-instance-profile --instance-profile-name $ROLE_NAME
aws iam add-role-to-instance-profile --instance-profile-name $ROLE_NAME --role-name $ROLE_NAME

echo "IAM role created: $ROLE_NAME"
```

- [ ] **Step 2: Run script**

```bash
bash scripts/create_iam_role.sh nyc-taxi-analytics-dev
```

Expected: `IAM role created: nyc-taxi-analytics-ec2-role`

- [ ] **Step 3: Verify DuckDB can read S3 from EC2**

SSH into EC2 and run:

```bash
python3 -c "
import duckdb
conn = duckdb.connect()
conn.execute('INSTALL httpfs; LOAD httpfs;')
conn.execute('SET s3_region=\"ap-southeast-1\";')
conn.execute('SET s3_use_credential_chain=true;')
rows = conn.execute(\"SELECT COUNT(*) FROM read_parquet('s3://nyc-taxi-analytics-dev/kpi_monthly_summary/*.parquet')\").fetchall()
print('Row count:', rows)
"
```

Expected: prints row count without credential errors.

- [ ] **Step 4: Commit**

```bash
git add scripts/create_iam_role.sh
git commit -m "feat: IAM role script for EC2 S3 read-only access"
```

---

## Task 13: Streamlit UI

**Files:**
- Create: `app.py`

- [ ] **Step 1: Write `app.py`**

```python
import json
import logging
import altair as alt
import pandas as pd
import streamlit as st
from analytics_agent.config import SCHEMA_REGISTRY_PATH
from analytics_agent.registry import load_registry, validate_registry
from analytics_agent.pipeline import run_pipeline

logging.basicConfig(level=logging.INFO, format="%(message)s")

@st.cache_resource
def get_registry():
    registry = load_registry(SCHEMA_REGISTRY_PATH)
    validate_registry(registry)
    return registry

def render_chart(chart_spec: dict, rows: list[dict]) -> None:
    if not chart_spec or not rows:
        return
    df = pd.DataFrame(rows)
    chart_type = chart_spec.get("type")
    x = chart_spec.get("x")
    y = chart_spec.get("y")
    series = chart_spec.get("series") or []

    try:
        if chart_type == "table":
            st.dataframe(df, use_container_width=True)
            return

        base = alt.Chart(df)
        color = alt.Color(f"{series}:N") if series and series in df.columns else alt.value("#4C78A8")

        if chart_type == "bar":
            chart = base.mark_bar().encode(x=f"{x}:O", y=f"{y}:Q", color=color)
        elif chart_type == "line":
            chart = base.mark_line(point=True).encode(x=f"{x}:O", y=f"{y}:Q", color=color)
        elif chart_type == "pie":
            # Altair has no native pie — render as horizontal bar
            chart = base.mark_bar().encode(y=alt.Y(f"{x}:N", sort="-x"), x=f"{y}:Q", color=color)
        else:
            st.warning(f"Unknown chart type: {chart_type}")
            return

        st.altair_chart(chart.properties(width="container"), use_container_width=True)
    except Exception as e:
        st.warning(f"Chart could not be rendered: {e}")

def main():
    st.set_page_config(page_title="NYC Taxi Analytics", layout="wide")
    st.title("NYC Taxi Analytics Agent")
    st.caption("Ask a question about NYC yellow cab trip data.")

    try:
        registry = get_registry()
    except Exception as e:
        st.error(f"Failed to load schema registry: {e}")
        return

    question = st.text_input("Your question", placeholder="e.g. show monthly revenue trend")

    if question:
        with st.spinner("Thinking..."):
            result = run_pipeline(question, registry)

        if result.error:
            st.error(f"Error: {result.error}")
        elif result.clarification:
            st.info(result.clarification)
        elif result.summary:
            st.markdown(result.summary)
            if result.chart_spec and result.log.get("query", {}).get("row_count", 0) > 0:
                rows = []  # rows not stored in result — re-fetch from log is not possible
                # chart_spec is validated; render placeholder message if no rows available
                st.caption("Chart data not available in demo mode — connect pipeline rows through for full rendering.")
            st.caption(f"Correlation ID: `{result.correlation_id}`")

        with st.expander("Debug log"):
            st.json(result.log)

if __name__ == "__main__":
    main()
```

> **Note:** The pipeline result currently does not carry the raw rows through to the UI (they stay in the log). In Task 14 we wire the rows through `PipelineResult` so chart rendering works end-to-end.

- [ ] **Step 2: Commit**

```bash
git add app.py
git commit -m "feat: Streamlit UI with chart rendering and debug log expander"
```

---

## Task 14: Wire Rows Through Pipeline to UI

**Files:**
- Modify: `analytics_agent/pipeline.py` — add `rows` to `PipelineResult`
- Modify: `app.py` — use `result.rows` for chart rendering

- [ ] **Step 1: Add `rows` field to `PipelineResult`**

In `analytics_agent/pipeline.py`, update the dataclass:

```python
@dataclass
class PipelineResult:
    correlation_id: str
    summary: str | None = None
    chart_spec: dict | None = None
    rows: list[dict] = field(default_factory=list)
    clarification: str | None = None
    error: str | None = None
    log: dict = field(default_factory=dict)
```

And in `run_pipeline`, update the success return:

```python
        return PipelineResult(
            correlation_id=correlation_id,
            summary=summarize_result.summary,
            chart_spec=summarize_result.chart_spec,
            rows=query_result.rows,
            log=log,
        )
```

- [ ] **Step 2: Update `app.py` chart section**

Replace the chart rendering block:

```python
        elif result.summary:
            st.markdown(result.summary)
            if result.chart_spec and result.rows:
                render_chart(result.chart_spec, result.rows)
            elif result.chart_spec and not result.rows:
                st.caption("No rows to chart.")
            st.caption(f"Correlation ID: `{result.correlation_id}`")
```

- [ ] **Step 3: Run pipeline tests to confirm no regression**

```bash
pytest tests/test_pipeline.py -v
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add analytics_agent/pipeline.py app.py
git commit -m "feat: wire rows through PipelineResult to enable chart rendering in UI"
```

---

## Task 15: Golden Question Eval + Security Tests

**Files:**
- Create: `tests/test_golden_questions.py`
- Create: `tests/test_security.py`

- [ ] **Step 1: Write `tests/test_security.py`**

```python
import pytest
from analytics_agent.agents.query import validate_sql, SQLValidationError

KNOWN = {"kpi_monthly_summary"}

def test_prompt_injection_semicolon():
    with pytest.raises(SQLValidationError):
        validate_sql("SELECT 1; DROP TABLE kpi_monthly_summary", "kpi_monthly_summary", KNOWN)

def test_read_parquet_injection():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql("SELECT * FROM read_parquet('s3://evil/bucket/*.parquet')", "kpi_monthly_summary", KNOWN)

def test_httpfs_injection():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql("SELECT * FROM read_csv_auto('http://evil.com/data.csv')", "kpi_monthly_summary", KNOWN)

def test_copy_export_blocked():
    with pytest.raises(SQLValidationError, match="file function"):
        validate_sql("COPY (SELECT * FROM kpi_monthly_summary) TO '/tmp/out.csv'", "kpi_monthly_summary", KNOWN)

def test_ddl_drop_blocked():
    with pytest.raises(SQLValidationError):
        validate_sql("DROP TABLE kpi_monthly_summary", "kpi_monthly_summary", KNOWN)

def test_cross_table_blocked():
    with pytest.raises(SQLValidationError, match="not allowed"):
        validate_sql("SELECT * FROM secret_passwords", "kpi_monthly_summary", KNOWN)

def test_valid_query_passes():
    validate_sql(
        "SELECT month, revenue FROM kpi_monthly_summary ORDER BY revenue DESC LIMIT 10",
        "kpi_monthly_summary", KNOWN
    )
```

- [ ] **Step 2: Write `tests/test_golden_questions.py`**

This test requires Ollama + S3 to be running. Mark with `@pytest.mark.integration`:

```python
import json, pytest
from pathlib import Path
from analytics_agent.pipeline import run_pipeline
from analytics_agent.registry import load_registry, validate_registry

pytestmark = pytest.mark.integration

@pytest.fixture(scope="module")
def registry():
    r = load_registry("schema_registry.json")
    validate_registry(r)
    return r

GOLDEN = [
    ("show monthly revenue trend",         "kpi_monthly_summary"),
    ("which hour has the most trips",      "fact_trips_hourly"),
    ("show weekly trip count",             "kpi_weekly_trends"),
    ("revenue by borough",                 "kpi_borough_comparison"),
    ("most popular routes",                "route_popular_routes"),
    ("zone performance by revenue",        "kpi_zone_performance"),
    ("show peak hour heatmap",             "ops_peak_hours_heatmap"),
    ("payment type breakdown",             "kpi_payment_trends"),
    ("daily overview for recent days",     "kpi_daily_overview"),
    ("vendor performance by trip count",   "kpi_vendor_performance"),
]

@pytest.mark.parametrize("question,expected_table", GOLDEN)
def test_golden_question(question, expected_table, registry):
    result = run_pipeline(question, registry)
    assert result.error is None, f"Pipeline error for '{question}': {result.error}"
    assert result.summary is not None, f"No summary for '{question}'"
    selected = result.log.get("supervisor", {}).get("table_selected")
    assert selected == expected_table, f"'{question}': expected {expected_table}, got {selected}"
```

- [ ] **Step 3: Run unit + security tests (no Ollama needed)**

```bash
pytest tests/ -v -m "not integration"
```

Expected: all PASS.

- [ ] **Step 4: Run golden questions (requires Ollama + S3)**

```bash
pytest tests/test_golden_questions.py -v -m integration
```

Expected: at least 8/10 PASS (acceptance threshold from spec).

- [ ] **Step 5: Commit**

```bash
git add tests/test_security.py tests/test_golden_questions.py
git commit -m "test: security validator tests and golden question integration eval"
```

---

## Task 16: Final Wiring + Demo Run

- [ ] **Step 1: Create `.env` from `.env.example`**

```bash
cp .env.example .env
# edit .env: set ANALYTICS_S3_BUCKET, OLLAMA_BASE_URL pointing to EC2
```

- [ ] **Step 2: Run full unit test suite**

```bash
pytest tests/ -v -m "not integration"
```

Expected: all PASS.

- [ ] **Step 3: Run Streamlit locally**

```bash
streamlit run app.py
```

Open `http://localhost:8501`. Ask: `show monthly revenue trend`

Expected: summary text appears + bar chart renders.

- [ ] **Step 4: Test each golden question manually in the UI**

Ask each of the 10 golden questions. Verify:
- Summary text is present and non-empty
- Chart renders (or graceful "no chart" message)
- Debug log shows correct table selected and SQL generated

- [ ] **Step 5: Final commit**

```bash
git add .
git commit -m "feat: complete NYC taxi analytics agent pipeline"
```
