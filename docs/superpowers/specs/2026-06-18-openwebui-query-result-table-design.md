# Open WebUI Query Result Table Design

Date: 2026-06-18
Status: Approved for implementation planning
Scope: WebUI chat query-result-table experience only

## Overview

Add a query result table experience to the existing Open WebUI analytics pipe.
When an employee asks an analytics question, the response can show a structured,
interactive table in the chat response instead of only text or a chart.

This is a presentation enhancement inside `openwebui/filter_analytics.py`. It
does not add a database browser, catalog, dashboard, new service, or server-side
query API.

## Goals

- Show query result rows as an interactive table in Open WebUI chat.
- Keep summary text first, followed by chart and/or table artifacts.
- Let the prompt decide whether the response shows chart only, table only, both,
  or text only.
- Reuse the existing Open WebUI HTML artifact and `embeds` event path.
- Keep the 1-day version view-only and bounded to the existing 200-row cap.

## Non-Goals

- Full database or catalog UI.
- Dashboard persistence.
- Server-side pagination.
- Browser callbacks to DuckDB, S3, or the pipe backend.
- CSV or file download.
- New Open WebUI frontend components.
- New Kubernetes service, Helm chart, or ArgoCD app.
- Heavy PII masking for v1. The current dataset is NYC taxi revenue data in an
  employee-only WebUI.

## Current Context

The analytics pipe already:

- Routes chat, ambiguous, and analytics prompts through `classify_intent`.
- Runs the supervisor, query, and summary path inside `openwebui/filter_analytics.py`.
- Queries S3 Parquet through DuckDB and caps returned rows at 200.
- Streams the summary through `StreamingResponse`.
- Emits chart HTML through Open WebUI `embeds` events.
- Treats `chart_spec.type == "table"` as "no chart artifact" today.

The table feature should reuse these boundaries instead of introducing a new
response channel.

## Architecture

The analytics path remains:

```text
user prompt
  -> classify_intent
  -> supervisor selects table
  -> query agent generates validated SQL
  -> DuckDB returns capped rows
  -> presentation selector chooses chart/table/both/text
  -> summary streams first
  -> chart and/or table artifacts render below the summary
```

New helpers:

- `_select_presentation_mode(question, rows)` decides `chart`, `table`, `both`,
  or `text`.
- `build_table_artifact(rows, metadata)` returns a self-contained HTML table
  artifact.

Changed orchestration:

- `_stream_analytics` prepares the needed artifacts after rows are available.
- It streams the summary before emitting artifacts.
- It emits chart and table HTML through the existing `embeds` event path.

The browser table only interacts with embedded, capped rows. It never calls
DuckDB, S3, or the backend.

## Presentation Intent

Presentation mode is deterministic when the prompt is explicit:

| Prompt signal | Mode |
| --- | --- |
| `table`, `rows`, `list`, `tabular`, `show data`, `result table` | `table` |
| `chart`, `graph`, `plot`, `visualize`, `trend line`, `bar chart` | `chart` |
| Both table and chart signals | `both` |
| Empty rows | `text` |
| No display preference | Ask chart selector; use table if selector returns `table` |

Examples:

- `show monthly revenue trend` -> chart
- `show monthly revenue trend as a table` -> table
- `show revenue by borough with chart and table` -> both
- `list top 20 pickup zones by revenue` -> table

If the chart selector returns an invalid chart spec and rows exist, fall back to
table.

## Internal Presentation Object

For v1, this is pipe-internal rather than a public API contract:

```json
{
  "mode": "chart|table|both|text",
  "table": {
    "columns": [
      {"key": "month", "label": "month", "type": "string"},
      {"key": "revenue", "label": "revenue", "type": "number"}
    ],
    "rows": [
      {"month": "2026-01", "revenue": 12345.67}
    ],
    "row_count": 42,
    "row_cap": 200,
    "capped": false,
    "data_as_of": null
  },
  "chart_spec": {
    "type": "bar|line|pie",
    "x": "month",
    "y": "revenue"
  }
}
```

Rules:

- `rows` are already capped before rendering.
- `columns` are inferred from returned rows.
- `data_as_of` is optional and included only if current metadata provides it.
- No CSV or download field exists in v1.
- No server-side pagination cursor exists in v1.

## Table Artifact UX

The table renders inside a self-contained HTML artifact using vanilla
HTML/CSS/JavaScript. Do not use DataTables, Tabulator, Grid.js, React, jQuery,
npm packages, or a CDN for v1.

The artifact contains:

- Toolbar with global search and row count text.
- Sticky table header.
- Sortable columns.
- Client-side pagination.
- Page size selector with 10, 25, 50, and 100 rows.
- Default page size of 25.
- Horizontal scroll for wide results.
- Stable row height with long values truncated and exposed via `title`.
- Empty state: `No rows returned`.
- Capped state: `Showing first 200 rows`.

Interaction rules:

- Search filters across embedded cell values.
- Sorting is client-side only.
- Pagination is client-side only.
- The artifact is view-only.
- No CSV, export, or copy-all control appears in v1.

## Limits And Safety

The v1 safety model is intentionally lightweight because the current dataset is
NYC taxi revenue data and the WebUI is employee-only.

Limits:

- 200-row backend cap.
- No CSV or download.
- No server-side pagination.
- No browser callbacks.
- No external scripts in the table artifact.

The primary v1 risk is usability, not sensitive data exposure. The row cap and
view-only artifact prevent the table from becoming a bulk data extraction
surface.

## Error Handling

| Scenario | Behavior |
| --- | --- |
| Empty rows | Stream text summary only; no artifact |
| Table artifact generation fails | Keep summary; add a short table render failure note |
| Chart requested but chart spec invalid | Fall back to table if rows exist |
| Both requested and one artifact fails | Render the artifact that succeeds |
| Open WebUI emitter unavailable | Summary still works; artifacts are skipped |
| Result capped | Show `Showing first 200 rows` in the table toolbar |

## Fit With Existing Analytics Path

This feature stays inside `openwebui/filter_analytics.py`.

What changes:

- Add `_select_presentation_mode(question, rows)`.
- Add `build_table_artifact(rows, metadata)`.
- Replace the current chart-only artifact branch with chart/table/both handling.
- Change artifact ordering so summary streams first and artifacts appear below.
- Treat `chart_spec.type == "table"` as table rendering, not "render nothing".

What stays unchanged:

- Intent routing.
- Supervisor table selection.
- SQL generation and validation.
- DuckDB S3 execution.
- 200-row cap.
- Summary streaming.
- Pipe deployment in Open WebUI.

## Testing

Unit tests:

- `_select_presentation_mode` returns chart/table/both/text for representative
  prompts.
- `build_table_artifact` escapes cell values.
- `build_table_artifact` includes search, sort, pagination, and no download
  control.
- `chart_spec.type == "table"` produces a table artifact.
- Invalid chart spec falls back to table when rows exist.

Stream tests:

- Table prompt emits a table artifact.
- Chart prompt emits a chart artifact.
- Both prompt emits chart and table artifacts.
- Summary chunks are produced before artifact emission.
- Emitter absence does not break summary streaming.

Manual WebUI smoke tests:

- `show monthly revenue trend`
- `show monthly revenue trend as a table`
- `show revenue by borough with chart and table`

Expected behavior:

- Summary appears first.
- Requested artifact appears below the summary.
- Table search, sort, and pagination work over the displayed capped rows.

## Implementation Notes

- The current code passes `row_cap` into `_stream_analytics`, but `_run_query`
  uses the module constant `ROW_CAP`. For this v1, fixed 200-row behavior is
  acceptable. A later cleanup can wire the Valve through if configurable caps
  become important.
- The current chart branch emits artifacts before summary streaming. This must
  change so artifacts are prepared first, summary streams, then artifacts emit.
- Existing chart HTML uses inline JavaScript. The table artifact can use the
  same self-contained artifact pattern.
