# NYC Taxi Analytics Agent — System Design

Date: 2026-06-15
Status: Approved for implementation

---

## 1. Overview

This document describes a multi-agent natural-language analytics system over the NYC taxi golden dataset stored as Parquet files on S3.

A user asks a plain-English question. A three-agent pipeline — supervisor, query, summarize — maps that question to the right table, generates validated SQL, executes it via DuckDB against S3, and returns a business summary plus a chart specification.

The primary goal is a learning and portfolio project that demonstrates:
- Why schema discovery must be separated from SQL generation (the Uber insight)
- How to enforce security boundaries in an LLM-driven query pipeline
- How to make each agent narrow, testable, and independently explainable

---

## 2. Scope

### 2.1 In Scope (v1)

- Natural-language questions answered from pre-aggregated Parquet tables on S3
- Three-agent pipeline: supervisor → query → summarize
- Schema registry built once from local Parquet metadata, stored as JSON
- DuckDB as the SQL execution engine against S3
- Local LLM served via Ollama on EC2 (GPU instance)
- Structured JSON logging with correlation ID per request
- Simple demo UI (Streamlit) rendering summary + chart

### 2.2 Out of Scope (v1)

- Multi-table JOIN queries
- Write-back or mutation operations
- Authentication on the query interface
- S3 throttling retry / partial read detection
- Dynamic context window management (registry is kept under 3000 tokens by design — see §5.3)
- Dashboard persistence

---

## 3. Dataset

Golden dataset: NYC taxi trip data, pre-processed into 32 Parquet table directories under `docs/DB/files_list/`.

Three tiers:
- **Star schema** — `fact_trips_daily`, `fact_trips_hourly`, `dim_zone`, `dim_vendor`, `dim_date`, etc. Raw grain, may need filters.
- **Pre-aggregated KPIs** — `kpi_daily_overview`, `kpi_monthly_summary`, `kpi_zone_performance`, `kpi_weekly_trends`, etc. Already joined and computed, single-table SELECT sufficient.
- **Domain aggregates** — `route_popular_routes`, `ops_peak_hours_heatmap`, `dq_validation_summary`, etc. Shaped for a specific question type.

Total size: ~2MB across 43 Parquet files. Small enough to demo interactively, complex enough (32 tables) to demonstrate schema registry value.

---

## 4. Architecture

```
User question
    → Supervisor Agent
        reads schema registry
        selects table + confidence + reasoning
        if low confidence → clarification response (stop)
    → Query Agent
        receives table schema slice only
        generates SELECT SQL
        SQL validator checks safety
        DuckDB executes against s3://bucket/{table}/*.parquet
        returns rows (capped at 200)
    → Summarize Agent
        receives question + rows
        writes summary + chart spec JSON
    → Response
        summary text + rendered chart
```

Each agent calls Ollama via the OpenAI-compatible API (`/v1/chat/completions`). Python orchestration owns the pipeline state, validation, and error handling — the LLM only produces text output.

---

## 5. Schema Registry

Built once locally before S3 upload by scanning all Parquet file metadata.

### 5.1 Structure

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
    "example_questions": [
      "show monthly revenue trend",
      "which month had the most trips"
    ]
  }
}
```

### 5.2 Build Process

- Script reads Parquet schema metadata from each table directory
- One entry per table, column names and types extracted automatically
- Description and example questions written manually (one-time effort)
- Output saved as `schema_registry.json`, committed to repo, uploaded to S3 alongside data

### 5.3 Context Budget

`qwen2.5-coder:7b` default context window in Ollama: 2048 tokens. Ollama must be started with `--ctx-size 8192` (fits comfortably in T4 16GB VRAM with 7B quantized model).

Registry token estimate: 32 tables × ~40 tokens per entry (name + description + columns + examples) ≈ 1280 tokens. Plus system prompt (~400 tokens) and user question (~50 tokens) = ~1730 tokens total supervisor input, well within 8192.

If the registry grows beyond 32 tables in a future version, trim `example_questions` first (saves ~15 tokens/table).

### 5.4 Startup Health Check

On service start, validate that:
- `schema_registry.json` loads and parses without error
- Every table name in the registry has a corresponding S3 path

On service start, validate that:

## 6. Agent Design

### 6.1 Supervisor Agent

**Input:** user question + full schema registry JSON

**System prompt includes:**
- Domain context: NYC yellow cab trip analytics dataset
- Table tier descriptions: what KPI tables are vs fact tables vs domain aggregates
- Instruction to prefer pre-aggregated KPI tables for summary questions
- Borough naming conventions used in the dataset
- Output format requirement (structured JSON)

**Output (structured JSON):**
```json
{
  "table": "kpi_monthly_summary",
  "confidence": "high",
  "reasoning": "Question asks for monthly revenue trend; kpi_monthly_summary is pre-aggregated by month with revenue column."
}
```

**Validation before proceeding:**
- `confidence` must be `"high"` or `"low"` — any other value is logged as `unexpected_confidence_value` and treated as `"low"`
- `table` must exist in schema registry
- If `confidence` is `"low"` → return clarification question to user, stop pipeline

The system prompt must instruct the model explicitly: *"You must output exactly one of: high, low. No other values."* Unexpected values logged separately so repeated occurrences surface as a quality regression, not silent noise.

**Multi-table detection:**
- If question uses language implying a JOIN (e.g., "compare vendors by zone") and no single table covers it → treat as low confidence, explain v1 limitation

### 6.2 Query Agent

**Input:** user question + schema slice for the selected table only (not full registry)

**System prompt includes:**
- Domain context: NYC taxi data, UTC timestamps, borough names
- Revenue definition: `total_fare_amount` excluding tips
- Peak hours definition: 7–9am and 5–8pm
- Instruction to write a single SELECT statement, no markdown fences, no explanations
- Column names and types from the schema slice

**Output:** raw SQL string (no markdown, no explanation)

**SQL Validator (Python, runs before DuckDB):**

Must pass all checks or the SQL is rejected:
- Starts with `SELECT` (case-insensitive)
- No semicolon-chained statements
- Table name matches the supervisor-selected table (exact string match)
- No DDL keywords: `DROP`, `CREATE`, `INSERT`, `UPDATE`, `DELETE`, `ALTER`, `TRUNCATE`
- No DuckDB file functions: `read_parquet(`, `read_csv_auto(`, `COPY`, `EXPORT`
- No `httpfs` or external path references
- Reject if SQL contains the user's raw question text (injection guard)

**DuckDB execution:**
```python
duckdb.sql(f"SELECT * FROM read_parquet('s3://bucket/{table}/*.parquet')")
```
Table name comes from the supervisor output, not user input.

**Query timeout:** 30 seconds hard limit on DuckDB execution.

### 6.3 Summarize Agent

**Input:** user question + rows as JSON (capped at 200 rows)

**System prompt includes:**
- Domain context: NYC taxi analytics, audience is operations/business users
- Revenue and metric definitions
- Instruction to write a concise business summary (2–4 sentences)
- Chart spec format specification
- If rows were capped: include a note in the summary

**Output (structured JSON):**
```json
{
  "summary": "Peak revenue occurs between 5–8pm on weekdays, with Manhattan generating 3x the fare revenue of outer boroughs.",
  "chart_spec": {
    "type": "bar",
    "x": "hour",
    "y": "revenue",
    "series": []
  },
  "capped": false
}
```

**Output validation:**
- `summary` must be non-empty string
- `chart_spec.type` must be one of: `bar`, `line`, `pie`, `table`
- `chart_spec.x` and `chart_spec.y` must reference column names present in the returned rows
- If validation fails → return summary only, no chart, log the failure

**Chart rendering in Streamlit:**

Streamlit uses `st.altair_chart` (via Altair/Vega-Lite) to render charts from the chart spec:
- `bar` → `mark_bar()`, x = `chart_spec.x`, y = `chart_spec.y`
- `line` → `mark_line()`, x = `chart_spec.x`, y = `chart_spec.y`
- `pie` → rendered as horizontal bar (Altair has no native pie; this is acceptable for v1)
- `table` → `st.dataframe(rows_df)` — ignores x/y, renders full row set as a table
- `series` field: if non-empty, used as the color encoding dimension for multi-series charts

If chart rendering raises an exception, catch it, log it, and display summary text only — never show a broken chart to the user.

---

## 7. Security Design

### 7.1 IAM / S3 Access

- EC2 instance uses an IAM instance profile (no static credentials)
- IAM role grants: `s3:GetObject` on the analytics bucket only
- No `s3:ListAllMyBuckets`, no write permissions, no cross-bucket access
- DuckDB configured with instance profile credentials via `httpfs`

### 7.2 SQL Injection Defense

Three layers:
1. Table name is sourced from the schema registry (supervisor output validated against registry), never from user input directly
2. SQL validator whitelist (see §6.2) blocks DDL, file functions, and external path references
3. DuckDB runs against read-only Parquet files — no DDL possible even if SQL slips validation

### 7.3 Resource Protection

- DuckDB query timeout: 30 seconds
- Row cap: 200 rows passed to summarize agent
- Ollama request timeout: 60 seconds per agent call
- End-to-end request timeout: 3 minutes (covers all three agents + DuckDB)

### 7.4 Data Safety

- Dataset contains no customer PII (NYC open taxi data)
- No user question text is interpolated into SQL before validator runs
- SQL text is never exposed in the UI response

---

## 8. Error Handling

| Failure | Behavior |
|---------|----------|
| Supervisor low confidence | Return clarification question, stop pipeline |
| Supervisor returns invalid confidence value | Treat as low confidence |
| Supervisor selects unknown table | Return error, log raw LLM output |
| LLM output is not valid JSON | Parse error caught, log raw response, return generic error |
| LLM output wraps SQL in markdown fences | Strip ` ```sql ``` ` before validation |
| SQL fails validator | Log SQL + reason, return "couldn't generate a valid query", never execute |
| DuckDB query timeout | Return retrieval timeout error, log table + SQL |
| DuckDB query error | Return retrieval error, log SQL (not exposed to user) |
| DuckDB returns empty rows | Summarize agent returns "no data found for this period" |
| 200-row cap hit | Summarize agent notes in summary: "results limited to 200 rows" |
| Chart spec references missing column | Return summary only, no chart, log failure |
| Summary is empty/whitespace | Return generic "could not generate summary" error |
| Ollama timeout | Return "model unavailable, try again" error |
| Multi-table question detected | Return explanation: "this question requires combining tables, not supported in v1" |
| S3 credential error | Caught as DuckDB error, logged with distinction from SQL errors |

---

## 9. Observability

Every request emits one structured JSON log entry covering the full pipeline.

### 9.1 Log Schema

```json
{
  "correlation_id": "uuid4",
  "question": "...",
  "supervisor": {
    "table_selected": "kpi_monthly_summary",
    "confidence": "high",
    "reasoning": "...",
    "latency_ms": 1240,
    "raw_response": "..."
  },
  "query": {
    "sql": "SELECT ...",
    "validator_passed": true,
    "row_count": 42,
    "capped": false,
    "latency_ms": 320,
    "duckdb_error": null
  },
  "summarize": {
    "chart_spec_valid": true,
    "latency_ms": 980,
    "raw_response": "..."
  },
  "total_latency_ms": 2540,
  "error": null,
  "timestamp": "2026-06-15T14:30:00Z"
}
```

Raw LLM response is always logged before parsing so failures are reproducible.

### 9.2 Key Metrics to Track

- Supervisor low-confidence rate (signals model table selection degrading)
- SQL validator rejection rate
- 200-row cap hit rate
- DuckDB error rate
- End-to-end P95 latency

---

## 10. Infrastructure

| Component | Choice | Notes |
|-----------|--------|-------|
| EC2 instance | `g4dn.xlarge` | T4 GPU, 16GB VRAM |
| Local LLM | `qwen2.5-coder:7b` via Ollama | OpenAI-compatible API on port 11434, started with `--ctx-size 8192` |
| Query engine | DuckDB + httpfs | Queries Parquet on S3 directly |
| Data storage | S3 bucket (private) | One directory per table, `*.parquet` glob |
| Demo UI | Streamlit | Renders summary + chart from chart spec |
| IAM | EC2 instance profile | `s3:GetObject` on analytics bucket only |

### 10.1 S3 Layout

Bucket name is configured via environment variable `ANALYTICS_S3_BUCKET` (e.g., `nyc-taxi-analytics-<team>-dev`). Never hardcoded. DuckDB paths are constructed as `s3://{ANALYTICS_S3_BUCKET}/{table}/*.parquet`.

```
s3://{ANALYTICS_S3_BUCKET}/
  schema_registry.json
  fact_trips_daily/
    *.parquet
  kpi_monthly_summary/
    *.parquet
  ...
```

---

## 11. Data Flow (Happy Path)

1. User types question in Streamlit UI
2. Python orchestrator assigns `correlation_id`, starts timer
3. Supervisor agent: question + registry → table selection
4. Validator checks table name exists in registry
5. Query agent: question + schema slice → SQL string
6. SQL stripped of markdown fences, passed to validator
7. DuckDB executes `SELECT ... FROM read_parquet('s3://...')` with 30s timeout
8. Row count checked; if > 200, cap and flag
9. Summarize agent: question + rows → summary + chart spec
10. Chart spec validated against returned column names
11. Streamlit renders summary text + chart
12. Structured log written with correlation ID

---

## 12. Testing Strategy

### 12.1 Unit Tests

- Schema registry builder: correct column types extracted from Parquet metadata
- SQL validator: passes/rejects each rule independently
- JSON parse layer: handles malformed JSON, markdown-wrapped SQL, empty responses
- Row cap logic: caps at 200, sets `capped: true` flag

### 12.2 Agent Integration Tests

- Supervisor selects correct table for representative questions
- Supervisor returns low confidence for ambiguous and multi-table questions
- Query agent generates syntactically valid SQL for each table tier
- Summarize agent produces non-empty summary and valid chart spec columns
- Full pipeline returns correct shape for 10 golden questions

### 12.3 Security Tests

- SQL injection via question text (prompt injection attempt)
- `read_parquet()` embedded in generated SQL → validator rejects
- DDL in generated SQL → validator rejects
- Unknown table name in supervisor output → pipeline stops

### 12.4 Failure Tests

- Ollama unavailable → clean error returned
- DuckDB timeout → clean error returned
- Empty result set → "no data found" returned, no hallucination
- 200-row cap → note included in summary

---

## 13. 4-Day Implementation Plan (Outline)

| Day | Focus |
|-----|-------|
| 1 | Upload Parquet to S3, build schema registry script, test DuckDB httpfs against S3 |
| 2 | Supervisor agent + SQL validator + Query agent, unit tests |
| 3 | Summarize agent, full pipeline wiring, structured logging with correlation ID |
| 4 | Streamlit UI + chart rendering, golden question eval, cleanup. Security tests deferred to Day 3 end if time allows. |

### 13.1 Golden Question Set (10 questions)

These questions must pass end-to-end before Day 4 is complete. One per major table tier:

| # | Question | Expected table |
|---|----------|---------------|
| 1 | Show monthly revenue trend | `kpi_monthly_summary` |
| 2 | Which hour has the most trips? | `fact_trips_hourly` |
| 3 | Show weekly trip count | `kpi_weekly_trends` |
| 4 | Revenue by borough this year | `kpi_borough_comparison` |
| 5 | What are the most popular routes? | `route_popular_routes` |
| 6 | Show zone performance by revenue | `kpi_zone_performance` |
| 7 | Show peak hour heatmap | `ops_peak_hours_heatmap` |
| 8 | Payment type breakdown | `kpi_payment_trends` |
| 9 | Daily overview for recent days | `kpi_daily_overview` |
| 10 | Show vendor performance | `kpi_vendor_performance` |

Acceptance threshold: 8/10 questions reach summarize agent with valid SQL and non-empty summary.

---

## 14. Portfolio Story

This project demonstrates three things explicitly:

1. **The Uber schema problem** — why you can't give a model raw database access and expect good SQL. The supervisor exists because table selection and SQL generation are different cognitive tasks requiring different context.

2. **Defense in depth for LLM-generated SQL** — three layers (table whitelist, SQL validator, read-only Parquet) so no single failure exposes the data.

3. **Observability as a first-class concern** — correlation IDs, raw response logging, and per-agent latency make the system debuggable without replaying the request.
