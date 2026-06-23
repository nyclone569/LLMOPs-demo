# SQL Agent Prompt + Retry Overhaul — Design

**Date:** 2026-06-23
**Component:** `openwebui/filter_analytics.py`, `schema_registry.json`
**Author:** sirfenrir

## Problem

The analytics agent produces invalid SQL on compound, conflicting-grain
questions. Reported case:

> "List the top 20 pickup zones by total taxi revenue, represent a chart
> of total revenue following pickup_borough and conclude it"

With `pickup_borough` (underscore), the query succeeds. With `pickup
borough` (space), DuckDB raises:

```
Binder Error: column "revenue" must appear in the GROUP BY clause or
must be part of an aggregate function.
```

## Root Cause

Two issues compose:

1. **Self-repair loop only catches structural validation errors.**
   `_run_query` (`filter_analytics.py:1234`) retries on
   `SQLValidationError` (DDL, file functions, foreign tables). It does
   NOT retry on DuckDB execution errors. SQL with a missing GROUP BY
   column passes structural validation, then dies at
   `conn.execute(sql_capped)` with `BinderException`. The exception
   bubbles past the loop with no retry and no error feedback to the LLM.

2. **The system prompt `_QUERY_SYSTEM` (`filter_analytics.py:1194`) is
   thin.** It does not state aggregation rules, has no few-shot examples,
   and gives no guidance on grain conflicts or pre-aggregated tables.
   When the user phrases a column as "pickup borough" instead of
   `pickup_borough`, the model interprets it as a natural-language
   "group by borough" instruction and emits a query that mixes a bare
   `revenue` column with a borough-grain GROUP BY.

A third contributing factor: most registry entries (e.g.
`route_top_pickup_zones`) lack curated metadata (`aliases`, `grain`,
`use_for`, `avoid_for`), so the SQL agent receives only column-name +
type, not semantic guidance.

## Goals

1. The reported case (pickup-borough variant) must succeed.
2. Future binder/catalog/parser errors trigger a single self-correction
   round with the error fed back to the LLM.
3. The agent's grain/aggregation reasoning is visible to the user.
4. The most-trafficked tables get curated metadata so the SQL agent has
   semantic context, not just column lists.
5. No streaming regression; no API changes; no new dependencies.

## Non-Goals

- Replacing the intent classifier or supervisor.
- Adding a new disambiguation tier in `Pipe.pipe`.
- Rewriting `_validate_sql` rules. Structural validation is unchanged.
- Multi-table queries / JOINs (still single-table by validator design).

## Architecture

```
question
  → classify_intent              (unchanged)
  → _stream_analytics
      → _select_table_candidates (unchanged)
      → _run_supervisor          (unchanged)
      → _run_query               (CHANGED: prompt + retry + plan/sql split)
      → yield "> **Plan:** …"    (NEW)
      → yield "> **SQL:** …"
      → yield "> **Result:** …"
      → _run_chart_spec ‖ _stream_summary  (parallel, unchanged)
```

Three change surfaces:

1. **`_QUERY_SYSTEM` constant** — rewrite as PLAN/SQL contract with
   GROUP BY rules, DuckDB dialect rules, domain rules, and 2 few-shot
   examples.
2. **`_run_query` retry loop** — extend the existing 1-shot self-repair
   to also catch `duckdb.Error` (covers `BinderException`,
   `CatalogException`, `ParserException`, `ConversionException`). Same
   2-attempt budget. New helper `_split_plan_and_sql`.
3. **`schema_registry.json`** — curate 9 tables with the same metadata
   shape that `kpi_zone_net_flow` already has.

No new modules, no new dependencies, no schema/API changes.

## Components

### 1. `_QUERY_SYSTEM` rewrite

Output contract changes from "SQL only" to "PLAN paragraph, then SQL on a
new line." Skeleton:

```
You are a SQL query agent for NYC yellow cab trip analytics on DuckDB
reading Parquet files on S3.

OUTPUT CONTRACT
First, write a short PLAN paragraph (2-4 lines) covering:
  - which columns from the table answer the question
  - the grain you are answering at (row-level vs aggregated)
  - any aggregation/GROUP BY you intend to use
  - if the question conflicts (e.g. asks for two grains), which one you
    chose and why
Then, on a new line, write "SQL:" followed by ONE SELECT statement.

GROUP BY RULES
- Every non-aggregated column in SELECT must appear in GROUP BY.
- If a column is already a measure on a pre-aggregated table (revenue,
  trip_count, avg_fare etc. on kpi_*/route_*/ops_*), do NOT re-aggregate
  unless rolling up to a coarser grain.
- When rolling up: SUM measures, AVG only ratios with care, COUNT(*)
  for trip_count rollups.

DUCKDB DIALECT
- Recent windows: CURRENT_DATE - INTERVAL 7 DAY (not DATE_SUB)
- Date parts: EXTRACT(month FROM date_col)
- No read_parquet(), httpfs, COPY, or file functions
- One SELECT statement, no semicolons, no DDL

DOMAIN
- Borough names: Manhattan, Brooklyn, Queens, Bronx, Staten Island
- Peak hours: 7-9 and 17-20 (24h)
- The revenue column is called `revenue` on most tables (16 tables) and
  `total_revenue` on a few (`fact_trips_daily`, `fact_trips_hourly_zone`,
  `kpi_monthly_summary`, `dq_*`). Use the exact name shown in the
  per-query Columns list.
- Pre-aggregated tables (`kpi_*`/`route_*`/`ops_*`/`fact_trips_borough`)
  already contain summed measures — select directly, do not re-aggregate
  unless rolling up to a coarser grain.

EXAMPLES

Q: top 20 pickup zones by total revenue, with a borough breakdown chart
Table: route_top_pickup_zones
PLAN: route_top_pickup_zones is pre-aggregated at zone grain. The user
asked for top 20 zones AND a borough chart — conflicting grain. I'll
answer at zone grain (more specific) and keep pickup_borough so the
chart agent can group it downstream.
SQL:
SELECT pickup_zone, pickup_borough, revenue
FROM route_top_pickup_zones
ORDER BY revenue DESC
LIMIT 20

Q: weekly revenue trend over the last 8 weeks
Table: fact_trips_daily
PLAN: fact_trips_daily is at day grain. Need to roll up to weeks and
aggregate revenue. Use DATE_TRUNC for the week bucket and a recent
window filter.
SQL:
SELECT DATE_TRUNC('week', pickup_date) AS week,
       SUM(total_revenue) AS revenue
FROM fact_trips_daily
WHERE pickup_date >= CURRENT_DATE - INTERVAL 56 DAY
GROUP BY 1
ORDER BY 1
```

Token cost: ~450 input tokens vs current ~120.

### 2. `_run_query` extended self-repair

**Connection re-use:** the DuckDB connection, `httpfs` install, S3 secret,
and `CREATE VIEW` happen ONCE outside the retry loop. Only the
`conn.execute(sql_capped)` call moves inside the loop. The VIEW
definition does not change between attempts (same table), so re-creating
the connection on each retry would just burn 2-4s on httpfs setup for
nothing.

Both `SQLValidationError` and `duckdb.Error` route through the same
2-attempt loop:

```python
import duckdb

def _execute_sql(conn, sql_capped):
    return conn.execute(sql_capped).fetchdf().to_dict(orient="records")

def _build_conn():
    conn = duckdb.connect(config={"memory_limit": "512MB", ...})
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    _create_s3_secret(conn, aws_region)
    conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
    return conn

with ThreadPoolExecutor(max_workers=1) as executor:
    conn = _build_conn()  # one-time setup, outside the retry loop
    try:
        for attempt in range(2):
            raw = _llm_chat(messages, ...)
            stripped = _strip_fences(raw)              # fences first
            plan, sql = _split_plan_and_sql(stripped)  # then split
            sql = _normalize_duckdb_sql(sql.rstrip(";").strip())
            try:
                _validate_sql(sql, table, set(registry.keys()))
                sql_capped = _wrap_with_limit(sql)
                future = executor.submit(_execute_sql, conn, sql_capped)
                rows = future.result(timeout=DUCKDB_TIMEOUT)
                return {"sql": sql, "plan": plan, "rows": rows[:ROW_CAP], "capped": ...}
            except (SQLValidationError, duckdb.Error) as exc:
                if attempt == 1:
                    raise
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": _retry_prompt(exc, table)})
    finally:
        conn.close()
```

Trade-off accepted: the 30s `DUCKDB_TIMEOUT` now gates each attempt
independently rather than the request as a whole. Worst-case latency
discussed in the Risks table.

`_retry_prompt(exc, table)` returns one shared template regardless of
whether the exception came from the validator or DuckDB — the error
message itself carries enough context:

> Your SQL was rejected: `{exc}`. Re-read the GROUP BY rules and the
> columns list, fix the issue, and rewrite as ONE SELECT against
> `{table}`. Return PLAN then SQL.

`_split_plan_and_sql(text)` is a small helper. Splits on `^SQL:` at the
**start of a line** (`re.MULTILINE`, case-insensitive), at the FIRST
match. Returns `(plan, sql_chunk)`. This intentionally does not match
`SQL:` mid-sentence (e.g. "I'll write SQL: a SELECT…" inside the plan
paragraph). If no anchored delimiter is found, returns `("", text)` —
graceful degradation if the model ignores the contract.

Order of operations matters: `_strip_fences` runs on the FULL raw
output BEFORE the split (see pseudocode above), so a model that wraps
the entire response in ```sql fences gets unwrapped first, then split.

### 3. `_stream_analytics` plan emission

After supervisor, before SQL. Use `.get("plan", "")` to maintain
backwards-compat with the 8 existing `_run_query` mock fixtures in
`tests/test_filter_pipeline.py` that don't include a `plan` key:

```python
plan = query_result.get("plan", "")
if plan:
    yield f"> **Plan:** {plan}\n"
yield f"> **SQL:**\n> ```sql\n> {sql}\n> ```\n"
```

### 4. `schema_registry.json` curation

Add `aliases / grain / use_for / avoid_for / example_questions /
description` to:

- `route_top_pickup_zones`
- `kpi_zone_performance`
- `fact_trips_daily`
- `fact_trips_hourly`
- `fact_trips_hourly_zone`
- `fact_trips_borough`
- `kpi_daily_overview`
- `kpi_borough_comparison`
- `kpi_payment_trends`

Mirrors `kpi_zone_net_flow`'s shape exactly. `_registry_as_prompt`
already surfaces these fields — no code changes for this part.

Example for `route_top_pickup_zones`:

```json
"description": "Top pickup zones pre-aggregated at zone grain with borough context.",
"aliases": ["top pickup zones", "busiest pickup zones", "popular pickup zones"],
"grain": "one row per pickup zone",
"use_for": [
  "ranking pickup zones by trips/revenue",
  "zone leaderboards with borough context"
],
"avoid_for": [
  "daily/hourly trends (no date column)",
  "borough-only rollups (use kpi_borough_comparison)"
],
"example_questions": [
  "top 20 pickup zones by revenue",
  "busiest pickup zones in Manhattan"
]
```

## Data Flow

### Happy path

```
question
  → _select_table_candidates → _run_supervisor
  → _run_query
      messages = [system_with_few_shots, user_with_table_and_columns]
      attempt 1:
        raw = LLM (PLAN + SQL)
        plan, sql = _split_plan_and_sql(raw)
        _validate_sql(sql, table, registry_tables)
        sql_capped = _wrap_with_limit(sql)
        rows = duckdb.execute(sql_capped)
        return {sql, plan, rows, capped}
  → yield "> **Plan:** …"
  → yield "> **SQL:** …"
  → yield "> **Result:** …"
  → _run_chart_spec ‖ _stream_summary
```

### Retry path

Both error gates flow into the same retry shape — one feedback template,
one place to maintain:

```
attempt 1 raises SQLValidationError OR duckdb.Error
   ↓
feedback: "Your SQL was rejected: {exc}. Re-read the GROUP BY rules
           and the columns list, fix the issue, and rewrite as ONE
           SELECT against {table}. Return PLAN then SQL."
   ↓
attempt 2 runs with full conversation:
   [system, user, assistant_first_try, user_with_error]
   ↓
attempt 2 fails → raise → caught by _stream_analytics
   ↓
yield "> **Error:** {msg}\n"
emitter status "Done"; return
```

### Invariants

- Both error gates share the same 2-attempt budget.
- One feedback shape, one place to maintain.
- Retry budget stays at 2 total. Three doubles worst-case latency on
  hard failures; literature shows diminishing returns past attempt 2.
- The model sees its first attempt verbatim in the retry messages.
- `_split_plan_and_sql` is best-effort. If the model ignores the
  contract, we treat everything as SQL and lose the plan but don't fail.

## Error Handling

| Failure | Where caught | User-visible behavior |
|---|---|---|
| Registry fetch fails first time | `_load_registry` | `> **Error:** Could not load schema registry — {e}` |
| Registry fetch fails after warm cache | `_load_registry` | Stale cache used silently |
| Supervisor returns unknown table | `_run_supervisor` | Confidence forced to "low" → top-3 candidates |
| LLM returns malformed PLAN/SQL | `_split_plan_and_sql` | Plan empty, SQL parsed from full output |
| Validator rejects SQL | retry loop, attempt 1 | Silent retry with error fed back |
| DuckDB binder/catalog/parser error | retry loop, attempt 1 | Silent retry with error fed back |
| Both attempts fail | `_run_query` raises | `> **Error:** {final exception}` in stream |
| DuckDB exceeds 30s | `FuturesTimeoutError` → `TimeoutError` | `> **Error:** DuckDB query exceeded 30s` |
| Summary stream fails | `_stream_summary` | Falls back to sync `_llm_chat` |
| Chart spec fails | `_run_chart_spec` returns None | Mode falls back to table or text |

## What Does Not Change

- `Pipe.pipe` routing logic.
- Streaming contract — same chunk types in the same order, with one
  extra `> **Plan:**` block before `> **SQL:**`.
- `_validate_sql` rules — structure-only, same as today.
- Row cap, LIMIT-detection at depth 0, ThreadPoolExecutor + 30s timeout.
- Existing tests in `test_filter_pipeline.py` and `test_filter_intent.py`.
- Schema registry shape — only adds curated fields, no key/type changes.

## Backwards Compatibility

The PLAN/SQL contract is a behavior change in LLM output, not in any
external API. Three protections:

1. `_split_plan_and_sql` falls back if no `SQL:` delimiter is found.
2. The validator and DuckDB will catch any SQL regardless of plan
   presence.
3. The `> **Plan:**` block is additive in the user-visible stream —
   Open WebUI renders it as another markdown blockquote.

## Testing

### `tests/test_filter_pipeline.py` — extend

| Test | What it verifies |
|---|---|
| `test_run_query_retries_on_duckdb_binder_error` | Mock `BinderException` on first execute, success on second. 2 LLM calls, retry message contains binder error verbatim. Patch is on the `conn.execute` instance method (since the connection is now built before the loop) so the patch applies regardless of thread. |
| `test_run_query_retries_on_catalog_error` | `CatalogException` (column doesn't exist). Second attempt succeeds. |
| `test_run_query_raises_after_two_duckdb_failures` | Both attempts raise. Raises `duckdb.Error`, exactly 2 LLM calls. |
| `test_run_query_validator_then_duckdb_error_in_one_session` | Attempt 0 fails validation (file function), attempt 1 passes validation but fails on DuckDB. Total = 2 LLM calls, 1 DuckDB execute. Confirms the budget is exhausted after 2 attempts regardless of which gate failed. |
| `test_run_query_extracts_plan_and_sql` | LLM returns "PLAN: …\nSQL: SELECT …". Returned dict has `plan` field, `sql` is just the SELECT. |
| `test_run_query_handles_missing_plan_delimiter` | LLM returns just SQL. `plan == ""`, no error. |
| `test_run_query_strips_fences_around_full_plan_and_sql` | LLM wraps the entire `PLAN:…\nSQL:…` block in a ```sql fence. Both plan and sql are extracted correctly. |
| `test_run_query_ignores_sql_colon_inside_plan_text` | Plan paragraph contains "I'll write SQL: a SELECT…" inline. The line-anchored split must NOT fire on this — only on the actual `SQL:` line. |
| `test_run_query_retry_message_includes_error` | Spy on second `_llm_chat` call. Includes assistant's first attempt + error string. |
| `test_run_query_reuses_connection_across_attempts` | Spy on `duckdb.connect`. Asserted to be called exactly once even when attempt 1 fails on DuckDB and attempt 2 succeeds. |
| `test_stream_analytics_yields_plan_block` | End-to-end stream: `> **Plan:**` arrives between `> **Table:**` and `> **SQL:**`. |
| `test_stream_analytics_omits_plan_block_when_empty` | If `plan == ""` (or missing in the result dict), no `> **Plan:**` line. |
| `test_stream_analytics_pickup_borough_regression` | Regression test for the exact reported failure. The user prompt "List the top 20 pickup zones by total taxi revenue, represent a chart of total revenue following pickup borough and conclude it" routed through table selection + supervisor + retry should NOT raise. First LLM SQL attempt mocked as the buggy bare-`revenue`-with-borough-GROUP-BY query that triggers `BinderException`; second attempt mocked as the corrected query. |

### `tests/test_filter_intent.py` — small additions

| Test | What it verifies |
|---|---|
| `test_query_system_prompt_contains_group_by_rule` | `"GROUP BY"` appears in `_QUERY_SYSTEM`. |
| `test_query_system_prompt_contains_few_shot_examples` | `"PLAN:"` and `"SQL:"` both appear. |

### `tests/test_schema_registry_curated.py` — new file

| Test | What it verifies |
|---|---|
| `test_curated_tables_have_required_metadata` | Each of 9 tables has non-empty `aliases`, `grain`, `use_for`, `avoid_for`. |
| `test_curated_tables_appear_in_registry_prompt` | `_registry_as_prompt` surfaces curated fields for the 9 tables. |

### What is NOT tested

- LLM behavior on the rewritten prompt — empirical quality question,
  validated manually post-deploy with the original failing prompt.
- DuckDB's binder behavior — mocked, not run for real.
- Exact prompt wording — only structural anchors (`GROUP BY`, `PLAN:`,
  `SQL:`).

### Test execution

All new tests run under existing `pytest`. No new dependencies, no
network. Mock surface stays the same: `httpx.post` for `_llm_chat`,
`duckdb.connect` for the executor.

Total: ~17 new tests, ~340 lines.

## Risks

| Risk | Mitigation |
|---|---|
| LLM ignores PLAN/SQL contract | `_split_plan_and_sql` graceful fallback; validator and DuckDB still gate the SQL. |
| Larger system prompt slows first token | ~430 input tokens added; well under any practical context budget. Latency impact is one-time per request. |
| Retry on DuckDB error grows worst-case latency | Worst case becomes: LLM(60s) + DuckDB(30s) + retry-LLM(60s) + retry-DuckDB(30s) = **180s** vs current 150s (DuckDB ran once). 30s added. Connection re-use keeps httpfs/secret/VIEW setup out of the retry path. Consider tightening attempt-2 DuckDB timeout to 15s in a follow-up if user complaints surface. |
| Registry curation introduces typos that confuse the LLM | New tests pin presence; a curated reviewer pass on each table catches semantic typos. |
| `_split_plan_and_sql` confused by `SQL:` in plan text | Anchored on `^SQL:` line-start (`re.MULTILINE`), so mid-sentence "SQL:" doesn't trigger. |
| LLM wraps entire response (PLAN + SQL) in ```sql fences | `_strip_fences` runs on the full raw output BEFORE the split. |
| Existing `_run_query` mocks in tests miss the new `plan` key | `_stream_analytics` reads `query_result.get("plan", "")` — old mocks keep working. |
| LLM-generated plan text injects HTML into Open WebUI | Open WebUI's markdown renderer sanitizes by default. Low risk; flagged for review only. |

## Open Questions

None. All clarifications resolved during brainstorming.

## Implementation Order

1. New tests (TDD) — failing tests for retry-on-DuckDB-error and
   plan/sql split.
2. `_split_plan_and_sql` helper + tests pass.
3. `_run_query` retry-on-`duckdb.Error`.
4. `_stream_analytics` plan emission.
5. `_QUERY_SYSTEM` rewrite + structural prompt tests.
6. `schema_registry.json` curation + curation tests.
7. Manual end-to-end verification with the original failing prompt.

## References

- [Google Cloud — Techniques for improving text-to-SQL](https://cloud.google.com/blog/products/databases/techniques-for-improving-text-to-sql/)
- [AWS — Best practices for prompt engineering with Llama 3 for text-to-SQL](https://aws.amazon.com/blogs/machine-learning/best-practices-for-prompt-engineering-with-meta-llama-3-for-text-to-sql-use-cases/)
- [MotherDuck — LangChain SQL Agent with DuckDB](https://motherduck.com/blog/langchain-sql-agent-duckdb-motherduck/)
- [MAGIC — Generating Self-Correction Guideline for In-Context Text-to-SQL](https://arxiv.org/html/2406.12692)
