# Analytics Schema Candidate Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conservative schema metadata and a pre-supervisor candidate router so explicit table-name prompts like `Show me table kpi zone net flow` select `kpi_zone_net_flow` with high confidence.

**Architecture:** Keep `schema_registry.json` as the source of truth. Add deterministic exact/lexical candidate selection before the LLM supervisor, pass only candidate tables to the supervisor when possible, and keep the full-registry supervisor path as fallback. Enrich registry generation with factual derived metadata and small curated metadata, without adding embeddings or a vector database in v1.

**Tech Stack:** Python 3.11, pytest, pyarrow Parquet schema reading, Open WebUI single-file pipe, DuckDB query execution unchanged

---

## File Structure

- Modify: `openwebui/filter_analytics.py`
  - Add text normalization and candidate-selection helpers.
  - Update registry prompt rendering to include optional metadata.
  - Update supervisor orchestration to skip the LLM for exact table/alias matches and use candidate registries before full fallback.
- Modify: `scripts/build_registry.py`
  - Add derived metadata fields and curated metadata overlay.
  - Regenerate `schema_registry.json` from `docs/DB/files_list`.
- Modify: `schema_registry.json`
  - Generated output with new metadata fields.
- Modify: `tests/test_filter_pipeline.py`
  - Add unit tests for candidate routing, prompt rendering, and exact-match supervisor behavior.
- Create: `tests/test_build_registry.py`
  - Add focused tests for derived metadata and curated metadata overlay.

---

### Task 1: Add Candidate Selection Helpers

**Files:**
- Modify: `openwebui/filter_analytics.py`
- Modify: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Add failing candidate-selection tests**

Append these tests near the existing filter pipeline helper tests in `tests/test_filter_pipeline.py`:

```python
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
```

```python
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
```

```python
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
```

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "select_table_candidates" -v
```

Expected: FAIL with `ImportError` or `AttributeError` because `_select_table_candidates` does not exist.

- [ ] **Step 3: Add candidate helper implementation**

Add this block in `openwebui/filter_analytics.py` after `_registry_as_prompt()`:

```python
EXACT_CANDIDATE_SCORE = 1000


def _normalize_match_text(value: str) -> str:
    """Normalize natural-language and schema labels for lexical matching."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _compact_match_text(value: str) -> str:
    """Normalize text so spaces, hyphens, and underscores compare equally."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _entry_match_texts(table: str, entry: dict) -> dict[str, list[str]]:
    columns = [column.get("name", "") for column in entry.get("columns", [])]
    return {
        "table_words": _normalize_match_text(table).split(),
        "aliases": _as_list(entry.get("aliases")),
        "columns": columns,
        "dimensions": _as_list(entry.get("dimensions")),
        "measures": _as_list(entry.get("measures")),
        "use_for": _as_list(entry.get("use_for")),
        "examples": _as_list(entry.get("example_questions")),
        "description": _as_list(entry.get("description")),
    }


def _score_candidate(question: str, table: str, entry: dict) -> tuple[int, list[str]]:
    question_norm = _normalize_match_text(question)
    question_words = set(question_norm.split())
    texts = _entry_match_texts(table, entry)
    score = 0
    reasons: list[str] = []

    table_word_hits = [word for word in texts["table_words"] if word in question_words]
    if table_word_hits:
        score += 20 * len(table_word_hits)
        reasons.append("table words matched: " + ", ".join(table_word_hits))

    for field, weight in (
        ("aliases", 80),
        ("columns", 25),
        ("dimensions", 25),
        ("measures", 30),
        ("use_for", 35),
        ("examples", 25),
        ("description", 10),
    ):
        for text in texts[field]:
            text_norm = _normalize_match_text(text)
            if not text_norm:
                continue
            text_words = set(text_norm.split())
            overlap = sorted(question_words & text_words)
            if text_norm in question_norm:
                score += weight
                reasons.append(f"{field} phrase matched: {text}")
            elif overlap:
                score += min(weight, 8 * len(overlap))
                reasons.append(f"{field} words matched: {', '.join(overlap)}")

    return score, reasons


def _select_table_candidates(question: str, registry: dict, limit: int = 8) -> list[dict]:
    """Return likely table candidates before calling the LLM supervisor."""
    question_compact = _compact_match_text(question)
    exact_matches = []

    for table, entry in registry.items():
        table_label = table.replace("_", " ")
        if _compact_match_text(table_label) in question_compact:
            exact_matches.append({
                "table": table,
                "score": EXACT_CANDIDATE_SCORE,
                "match_type": "exact_table_name",
                "reasons": ["normalized table name matched"],
            })
            continue

        for alias in _as_list(entry.get("aliases")):
            alias_compact = _compact_match_text(alias)
            if alias_compact and alias_compact in question_compact:
                exact_matches.append({
                    "table": table,
                    "score": EXACT_CANDIDATE_SCORE - 10,
                    "match_type": "exact_alias",
                    "reasons": [f"alias matched: {alias}"],
                })
                break

    if exact_matches:
        return sorted(exact_matches, key=lambda item: item["score"], reverse=True)[:limit]

    scored = []
    for table, entry in registry.items():
        score, reasons = _score_candidate(question, table, entry)
        if score > 0:
            scored.append({
                "table": table,
                "score": score,
                "match_type": "lexical_score",
                "reasons": reasons[:5],
            })

    return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]
```

- [ ] **Step 4: Run tests to verify candidate selection passes**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "select_table_candidates" -v
```

Expected: 4 PASS.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "feat: add analytics table candidate selection"
```

Expected: commit succeeds with only these two files staged.

---

### Task 2: Render Rich Registry Metadata Compactly

**Files:**
- Modify: `openwebui/filter_analytics.py`
- Modify: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Add failing prompt-rendering tests**

Append these tests in `tests/test_filter_pipeline.py`:

```python
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
    assert "avoid_for: daily trends because this table has no date column" in prompt
```

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "registry_as_prompt" -v
```

Expected: FAIL because optional metadata is not rendered yet.

- [ ] **Step 3: Replace `_registry_as_prompt` implementation**

Replace the current `_registry_as_prompt()` in `openwebui/filter_analytics.py` with:

```python
def _format_prompt_list(label: str, values: list[str], none_label: str | None = None) -> str | None:
    if values:
        return f"{label}: " + "; ".join(values)
    if none_label is not None:
        return f"{label}: {none_label}"
    return None


def _registry_as_prompt(registry: dict) -> str:
    lines = []
    for table, entry in registry.items():
        col_list = ", ".join(f"{c['name']}({c['type']})" for c in entry["columns"])
        parts = [
            f"- {table} [{entry['tier']}]: {entry['description']}",
        ]

        metadata_parts = [
            _format_prompt_list("aliases", _as_list(entry.get("aliases"))),
            f"grain: {entry['grain']}" if entry.get("grain") else None,
            _format_prompt_list("dimensions", _as_list(entry.get("dimensions"))),
            _format_prompt_list("measures", _as_list(entry.get("measures"))),
            _format_prompt_list("date_columns", _as_list(entry.get("date_columns")), none_label="none"),
            _format_prompt_list("use_for", _as_list(entry.get("use_for"))),
            _format_prompt_list("avoid_for", _as_list(entry.get("avoid_for"))),
            _format_prompt_list("examples", _as_list(entry.get("example_questions"))),
            f"columns: {col_list}",
        ]
        parts.extend(part for part in metadata_parts if part)
        lines.append(" | ".join(parts))
    return "\n".join(lines)
```

- [ ] **Step 4: Run prompt-rendering tests**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "registry_as_prompt" -v
```

Expected: 2 PASS.

- [ ] **Step 5: Run candidate tests again**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "select_table_candidates or registry_as_prompt" -v
```

Expected: 6 PASS.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "feat: include registry metadata in supervisor prompt"
```

Expected: commit succeeds with only these two files staged.

---

### Task 3: Route Exact Matches Before the Supervisor LLM

**Files:**
- Modify: `openwebui/filter_analytics.py`
- Modify: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Add failing exact-match orchestration test**

Append this async test in `tests/test_filter_pipeline.py`:

```python
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
```

- [ ] **Step 2: Add failing candidate-subset supervisor test**

Append this test in `tests/test_filter_pipeline.py`:

```python
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
```

- [ ] **Step 3: Run tests to verify exact-match orchestration fails**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "exact_table_match_skips_supervisor_llm or candidate_registry_only" -v
```

Expected: exact-match test FAILS because `_stream_analytics` still calls `_run_supervisor`; candidate-registry test may pass because `_run_supervisor` already accepts any registry dict.

- [ ] **Step 4: Add helper functions for synthetic supervisor and candidate registry**

Add this block after `_select_table_candidates()` in `openwebui/filter_analytics.py`:

```python
def _candidate_registry(registry: dict, candidates: list[dict]) -> dict:
    return {
        candidate["table"]: registry[candidate["table"]]
        for candidate in candidates
        if candidate.get("table") in registry
    }


def _supervisor_from_exact_candidate(candidate: dict) -> dict:
    reason = "; ".join(candidate.get("reasons", [])) or "exact table match"
    return {
        "table": candidate["table"],
        "confidence": "high",
        "reasoning": reason,
    }
```

- [ ] **Step 5: Update `_stream_analytics` table-selection block**

In `openwebui/filter_analytics.py`, replace the current supervisor call block:

```python
    if emitter:
        await emitter({"type": "status", "data": {"description": "Selecting table from registry...", "done": False}})
    try:
        supervisor = _run_supervisor(question, registry, litellm_url, litellm_model, api_key)
    except Exception as e:
        yield f"> **Error:** Table selection failed — {e}\n"
        if emitter:
            await emitter({"type": "status", "data": {"description": "Done", "done": True}})
        return
```

with:

```python
    if emitter:
        await emitter({"type": "status", "data": {"description": "Selecting table from registry...", "done": False}})

    candidates: list[dict] = []
    try:
        candidates = _select_table_candidates(question, registry)
        exact_candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate.get("match_type") in {"exact_table_name", "exact_alias"}
            ),
            None,
        )
        if exact_candidate:
            supervisor = _supervisor_from_exact_candidate(exact_candidate)
        else:
            prompt_registry = _candidate_registry(registry, candidates) if candidates else registry
            supervisor = _run_supervisor(question, prompt_registry, litellm_url, litellm_model, api_key)
    except Exception as e:
        yield f"> **Error:** Table selection failed — {e}\n"
        if emitter:
            await emitter({"type": "status", "data": {"description": "Done", "done": True}})
        return
```

- [ ] **Step 6: Run exact-match orchestration tests**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "exact_table_match_skips_supervisor_llm or candidate_registry_only" -v
```

Expected: 2 PASS.

- [ ] **Step 7: Run analytics stream regression tests**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "stream_analytics or pipe_analytics" -v
```

Expected: existing stream and pipe analytics tests PASS. If a mock expected `_run_supervisor` to be called, update the mock to use a question without exact table-name signal.

- [ ] **Step 8: Commit Task 3**

Run:

```bash
git add openwebui/filter_analytics.py tests/test_filter_pipeline.py
git commit -m "feat: route exact analytics table matches without llm"
```

Expected: commit succeeds with only these two files staged.

---

### Task 4: Enrich Registry Builder and Regenerate Registry

**Files:**
- Modify: `scripts/build_registry.py`
- Create: `tests/test_build_registry.py`
- Modify: `schema_registry.json`

- [ ] **Step 1: Add failing registry builder tests**

Create `tests/test_build_registry.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_registry import apply_curated_metadata, infer_column_roles


def test_infer_column_roles_identifies_dimensions_measures_and_dates():
    columns = [
        {"name": "pickup_date", "type": "date32[day]"},
        {"name": "location_id", "type": "int32"},
        {"name": "zone", "type": "string"},
        {"name": "trip_count", "type": "int64"},
        {"name": "total_revenue", "type": "double"},
        {"name": "pickup_hour", "type": "int64"},
    ]

    roles = infer_column_roles(columns)

    assert roles["date_columns"] == ["pickup_date"]
    assert "location_id" in roles["dimensions"]
    assert "zone" in roles["dimensions"]
    assert "trip_count" in roles["measures"]
    assert "total_revenue" in roles["measures"]
    assert "pickup_hour" in roles["dimensions"]
```

```python
def test_apply_curated_metadata_adds_zone_net_flow_semantics():
    entry = {
        "description": "Kpi Zone Net Flow - auto-generated, update manually",
        "tier": "kpi",
        "columns": [
            {"name": "zone", "type": "string"},
            {"name": "borough", "type": "string"},
            {"name": "net_flow", "type": "int64"},
        ],
        "example_questions": [],
        "dimensions": ["zone", "borough"],
        "measures": ["net_flow"],
        "date_columns": [],
    }

    result = apply_curated_metadata("kpi_zone_net_flow", entry)

    assert result["description"].startswith("Zone-level pickup/dropoff imbalance")
    assert "kpi zone net flow" in result["aliases"]
    assert result["grain"] == "one row per taxi zone"
    assert "zone pickup/dropoff imbalance" in result["use_for"]
    assert any("no date column" in item for item in result["avoid_for"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/pytest tests/test_build_registry.py -v
```

Expected: FAIL because `infer_column_roles` and `apply_curated_metadata` do not exist.

- [ ] **Step 3: Add metadata helpers to `scripts/build_registry.py`**

Insert this code below `TIER_MAP`:

```python
ID_COLUMN_SUFFIXES = ("_id", "_code")
DATE_TYPE_MARKERS = ("date", "timestamp")
DIMENSION_NAME_HINTS = {"year", "month", "day", "day_of_week", "week", "week_of_year", "quarter", "pickup_hour"}

CURATED_METADATA = {
    "kpi_zone_net_flow": {
        "description": "Zone-level pickup/dropoff imbalance and net flow metrics for NYC taxi zones.",
        "aliases": [
            "kpi zone net flow",
            "zone net flow",
            "net flow by zone",
            "zone inflow outflow",
        ],
        "grain": "one row per taxi zone",
        "use_for": [
            "zone pickup/dropoff imbalance",
            "zone net inflow and outflow analysis",
            "pickup revenue versus dropoff revenue by zone",
        ],
        "avoid_for": [
            "daily trend questions because this table has no date column",
            "hourly trend questions because this table has no hour column",
            "pickup-to-dropoff route pair questions because this table is zone-level, not route-pair grain",
        ],
        "example_questions": [
            "show table kpi zone net flow",
            "which zones have the largest pickup dropoff imbalance",
        ],
    },
}


def infer_column_roles(columns: list[dict]) -> dict:
    dimensions = []
    measures = []
    date_columns = []

    for column in columns:
        name = column["name"]
        lower_name = name.lower()
        lower_type = column["type"].lower()
        is_date = any(marker in lower_type for marker in DATE_TYPE_MARKERS)
        is_string_like = any(marker in lower_type for marker in ("string", "bool"))
        is_numeric = any(
            marker in lower_type
            for marker in ("int", "double", "float", "decimal")
        )
        is_identifier = lower_name.endswith(ID_COLUMN_SUFFIXES)
        is_time_part = lower_name in DIMENSION_NAME_HINTS

        if is_date:
            date_columns.append(name)
            dimensions.append(name)
        elif is_string_like or is_identifier or is_time_part:
            dimensions.append(name)
        elif is_numeric:
            measures.append(name)
        else:
            dimensions.append(name)

    return {
        "dimensions": dimensions,
        "measures": measures,
        "date_columns": date_columns,
    }


def apply_curated_metadata(table_name: str, entry: dict) -> dict:
    curated = CURATED_METADATA.get(table_name, {})
    result = {**entry, **curated}
    result["metadata_source"] = {
        "columns": "schema",
        "dimensions": "derived",
        "measures": "derived",
        "date_columns": "derived",
    }
    for field in ("description", "aliases", "grain", "use_for", "avoid_for", "example_questions"):
        if field in curated:
            result["metadata_source"][field] = "curated"
    return result
```

- [ ] **Step 4: Update `scan_table` to add metadata**

Replace the return block in `scan_table()` with:

```python
    columns = [
        {"name": field.name, "type": str(field.type)}
        for field in schema
    ]
    entry = {
        "description": f"{table_dir.name.replace('_', ' ').title()} - auto-generated, update manually",
        "tier": infer_tier(table_dir.name),
        "columns": columns,
        "example_questions": [],
        **infer_column_roles(columns),
    }
    return apply_curated_metadata(table_dir.name, entry)
```

- [ ] **Step 5: Run registry builder tests**

Run:

```bash
.venv/bin/pytest tests/test_build_registry.py -v
```

Expected: 2 PASS.

- [ ] **Step 6: Regenerate `schema_registry.json`**

Run:

```bash
.venv/bin/python scripts/build_registry.py --source docs/DB/files_list --output schema_registry.json
```

Expected: output lists all table directories with `OK` and ends with `Wrote 32 tables to schema_registry.json`.

- [ ] **Step 7: Verify generated registry contains enriched `kpi_zone_net_flow`**

Run:

```bash
.venv/bin/python - <<'PY'
import json
r = json.load(open("schema_registry.json"))
z = r["kpi_zone_net_flow"]
print(z["description"])
print(z["aliases"])
print(z["grain"])
print(z["date_columns"])
PY
```

Expected output contains:

```text
Zone-level pickup/dropoff imbalance and net flow metrics for NYC taxi zones.
['kpi zone net flow', 'zone net flow', 'net flow by zone', 'zone inflow outflow']
one row per taxi zone
[]
```

- [ ] **Step 8: Commit Task 4**

Run:

```bash
git add scripts/build_registry.py tests/test_build_registry.py schema_registry.json
git commit -m "feat: enrich analytics schema registry metadata"
```

Expected: commit succeeds with only these three files staged.

---

### Task 5: Verify Full Pipe Behavior and Guardrails

**Files:**
- Modify: `tests/test_filter_pipeline.py`

- [ ] **Step 1: Add focused regression test for low-confidence bypass**

Append this test in `tests/test_filter_pipeline.py`:

```python
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
```

- [ ] **Step 2: Run regression test**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -k "exact_alias_does_not_return_low_confidence_message" -v
```

Expected: 1 PASS.

- [ ] **Step 3: Run all filter pipeline tests**

Run:

```bash
.venv/bin/pytest tests/test_filter_pipeline.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Run registry builder tests**

Run:

```bash
.venv/bin/pytest tests/test_build_registry.py -v
```

Expected: 2 PASS.

- [ ] **Step 5: Run full local test suite**

Run:

```bash
.venv/bin/pytest -v
```

Expected: all tests PASS. If unrelated tests fail because of environment-only dependencies, capture the failing test names and error messages before proceeding.

- [ ] **Step 6: Manual deployment validation prompt list**

After the pipe and refreshed registry are deployed, run these prompts in Open WebUI:

```text
Show me table kpi zone net flow
Show me a table of daily NYC taxi revenue from 2024-03-25 to 2024-03-31.
Which zones have the biggest pickup dropoff imbalance?
Show monthly revenue trend.
```

Expected:

```text
Show me table kpi zone net flow
```

selects `kpi_zone_net_flow` with high confidence and proceeds to SQL generation.

```text
Show me a table of daily NYC taxi revenue from 2024-03-25 to 2024-03-31.
```

still returns the known 7-row date range from `fact_trips_daily`.

- [ ] **Step 7: Commit Task 5**

Run:

```bash
git add tests/test_filter_pipeline.py
git commit -m "test: cover exact analytics table prompt routing"
```

Expected: commit succeeds with only the regression test staged.

---

## Rollout Notes

- `schema_registry.json` must be uploaded to `s3://llmops-analytics-492372116094/schema_registry.json` after regeneration.
- The S3 Parquet layout does not change.
- The registry cache TTL defaults to 300 seconds. After uploading the registry, either wait for TTL expiry or restart the Open WebUI pod to force a fresh registry load.
- Existing `CURRENT_DATE` behavior stays unchanged. Static dataset tests should continue using explicit dates such as `2024-03-25` to `2024-03-31`.

## Self-Review Checklist

- Spec requirement: exact table-name prompt works without LLM confidence failure.
  - Covered by Task 1 and Task 3 tests.
- Spec requirement: candidate prompt can be narrower than full registry.
  - Covered by Task 3 candidate-registry path.
- Spec requirement: registry metadata is conservative and generated.
  - Covered by Task 4 builder helpers and regenerated registry.
- Spec requirement: no embedding/vector DB in v1.
  - Covered by file structure and tasks; no dependency changes.
- Spec requirement: SQL validation remains bound to real columns.
  - Existing `_run_query` and `_validate_sql` are unchanged by this plan.
