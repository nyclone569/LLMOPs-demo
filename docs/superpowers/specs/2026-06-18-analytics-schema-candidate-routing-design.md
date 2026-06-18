# Analytics Schema Candidate Routing — Design Spec

**Date:** 2026-06-18
**Status:** Draft
**Files:** `openwebui/filter_analytics.py`, `schema_registry.json`, `scripts/build_registry.py`, `tests/test_filter_pipeline.py`

## Problem

The NYC Taxi Analytics Pipe currently sends the full schema registry to the supervisor LLM and asks it to select one table. The registry is small enough for this to fit in context, but many table entries still have weak auto-generated descriptions and no examples. This makes explicit table requests brittle.

Observed failure:

```text
User: Show me table kpi zone net flow

Pipe: Table: kpi_zone_net_flow — The kpi_zone_net_flow table is not listed in the provided available tables. (confidence: low)
Pipe: I wasn't confident which data to use. Could you be more specific?
```

The selected table name is valid, so the failure is not DuckDB or S3. The failure is table-selection confidence and reasoning. The supervisor sees the table but lacks deterministic exact-name handling and richer registry metadata that would make this an obvious match.

## Goals

- Make explicit table-name requests deterministic.
- Reduce the number of registry tables the supervisor needs to read for normal analytics questions.
- Improve schema registry quality with conservative, data-grounded metadata.
- Keep the registry as the source of truth; do not hardcode table-specific knowledge in the system prompt.
- Keep SQL generation constrained to real table and column names.
- Keep the current full-registry supervisor path as a fallback while this matures.

## Non-Goals

- Do not add a vector database in this iteration.
- Do not add external embedding API calls.
- Do not change "last 7 days" or `CURRENT_DATE` behavior.
- Do not add multi-table SQL generation.
- Do not replace DuckDB, S3 layout, or Open WebUI pipe deployment.
- Do not rewrite the analytics pipe into multiple runtime files; the Open WebUI pipe stays single-file.

## Design

### 1. Registry Metadata Model

Extend each registry entry with optional metadata fields. Existing fields remain valid:

```json
{
  "description": "Zone-level pickup/dropoff imbalance and net flow metrics for NYC taxi zones.",
  "tier": "kpi",
  "columns": [
    {"name": "zone", "type": "string"},
    {"name": "borough", "type": "string"},
    {"name": "pickups", "type": "int64"},
    {"name": "dropoffs", "type": "int64"},
    {"name": "net_flow", "type": "int64"}
  ],
  "example_questions": [
    "show table kpi zone net flow",
    "which zones have the largest pickup dropoff imbalance"
  ],
  "aliases": [
    "kpi zone net flow",
    "zone net flow",
    "net flow by zone",
    "zone inflow outflow"
  ],
  "grain": "one row per taxi zone",
  "dimensions": ["zone", "borough"],
  "measures": ["pickups", "dropoffs", "net_flow", "net_flow_ratio", "imbalance_score", "pickup_revenue", "dropoff_revenue"],
  "date_columns": [],
  "use_for": [
    "zone pickup/dropoff imbalance",
    "zone net inflow and outflow analysis",
    "pickup revenue versus dropoff revenue by zone"
  ],
  "avoid_for": [
    "daily trend questions because this table has no date column",
    "hourly trend questions because this table has no hour column",
    "pickup-to-dropoff route pair questions because this table is zone-level, not route-pair grain"
  ],
  "metadata_source": {
    "columns": "schema",
    "dimensions": "derived",
    "measures": "derived",
    "aliases": "curated",
    "grain": "curated",
    "use_for": "curated",
    "avoid_for": "curated"
  }
}
```

Metadata safety rule: registry metadata may describe table capability, but it must not invent columns, joins, or facts. SQL generation still receives only the selected table and its real columns.

### 2. Registry Builder Enrichment

`scripts/build_registry.py` should continue reading Parquet schemas from `docs/DB/files_list`. It should add safe derived metadata for every table:

- `dimensions`: string, boolean, date, timestamp, and ID-like columns.
- `measures`: numeric columns that are not ID-like and not date parts.
- `date_columns`: date or timestamp columns.

Curated metadata should live in a small static map in the script for table-specific human knowledge:

- `description`
- `aliases`
- `grain`
- `use_for`
- `avoid_for`
- `example_questions`

The static map is acceptable here because it is registry curation, not prompt hardcoding. The generated `schema_registry.json` remains the source that the runtime pipe loads.

### 3. Candidate Routing

Add a pre-supervisor candidate selector:

```python
def _select_table_candidates(question: str, registry: dict, limit: int = 8) -> list[dict]:
    ...
```

Each returned candidate contains:

```python
{
    "table": "kpi_zone_net_flow",
    "score": 120,
    "match_type": "exact_table_name",
    "reasons": ["normalized table name matched"]
}
```

The selector has three gates:

1. **Exact lexical gate**
   - Normalize user text and table names by lowercasing and treating whitespace, underscores, and hyphens as equivalent.
   - If the user says `kpi zone net flow`, match `kpi_zone_net_flow`.
   - Exact table or alias matches should return the table with high confidence and skip the supervisor.

2. **Scored lexical gate**
   - Score matches from table words, aliases, column names, measures, dimensions, use cases, and example questions.
   - Return top candidates when exact matching is not enough.

3. **LLM supervisor gate**
   - Send only the top candidates to the supervisor.
   - If no candidate has useful score, fall back to the full registry.

Semantic embeddings are intentionally deferred. With 32 tables, better metadata plus lexical scoring should solve the current failure and keep the implementation simple.

### 4. Supervisor Contract

The system prompt should stay generic. It can be tightened with rules that apply to all tables:

- Select only from the available candidate tables.
- If the question explicitly names a candidate table or alias, select it with high confidence.
- Do not say a selected table is not listed if it appears in the candidate list.
- Use low confidence only when multiple candidate tables plausibly answer the question or no candidate table has enough evidence.

This is not table hardcoding; it is the routing contract.

### 5. Runtime Flow

New analytics table-selection flow:

```text
load registry from S3
select table candidates
if exact candidate:
    supervisor = synthetic high-confidence selection
else:
    supervisor = LLM over candidate registry
    if no candidates:
        supervisor = LLM over full registry fallback
if supervisor confidence low:
    ask clarification
else:
    run query agent
```

The query agent remains unchanged. It receives one selected table and that table's real columns.

### 6. Prompt Rendering

`_registry_as_prompt()` should include the new metadata only when present. Keep it compact:

```text
- kpi_zone_net_flow [kpi]: Zone-level pickup/dropoff imbalance and net flow metrics for NYC taxi zones.
  grain: one row per taxi zone
  aliases: kpi zone net flow; zone net flow
  dimensions: zone, borough
  measures: pickups, dropoffs, net_flow, imbalance_score, pickup_revenue, dropoff_revenue
  date_columns: none
  use_for: zone pickup/dropoff imbalance; zone net inflow and outflow analysis
  avoid_for: daily trend questions because this table has no date column
  columns: zone(string), borough(string), pickups(int64), ...
```

For candidate prompts, include only candidate tables. For full fallback prompts, keep all tables.

### 7. Error Handling

- If candidate selection fails due to unexpected registry shape, log traceback and fall back to full-registry supervisor.
- If exact table match is found, do not ask the LLM for confidence.
- If exact match selects a table whose S3 data is missing, existing DuckDB/S3 error handling applies.
- If metadata fields are missing, treat them as empty lists and keep existing behavior.

### 8. Testing Strategy

Add focused unit tests:

- `Show me table kpi zone net flow` exact-normalized match returns `kpi_zone_net_flow`.
- Exact alias match skips `_llm_chat` in `_run_supervisor`.
- Candidate prompt contains only matched candidates, not all registry tables.
- Weak/no-match question falls back to full registry.
- `_registry_as_prompt()` includes optional metadata when present and works with old registry entries.
- `scripts/build_registry.py` preserves current required fields and adds derived metadata.

Manual validation after deployment:

```text
Show me table kpi zone net flow
Show me a table of daily NYC taxi revenue from 2024-03-25 to 2024-03-31.
Which zones have the biggest pickup dropoff imbalance?
Show monthly revenue trend.
```

## Rollout

1. Add tests and candidate-selection helpers.
2. Update prompt rendering and supervisor flow.
3. Enrich the registry builder and regenerate `schema_registry.json`.
4. Run unit tests.
5. Deploy pipe and refreshed registry.
6. Validate explicit table-name prompt and existing daily revenue prompt.

## Open Questions

- Candidate `limit` defaults to 8 for now. If prompts remain noisy, reduce to 5 after test evidence.
- Semantic embeddings stay out of v1. Revisit only if lexical scoring fails for natural-language questions after metadata enrichment.
