# Pipe Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `openwebui/filter_analytics.py` from a Filter to a Pipe so the LLM short-circuit works correctly — analytics and ambiguous responses are returned directly without Ollama overwriting them.

**Architecture:** Replace `class Filter` with `class Pipe` and `def inlet()` with `async def pipe()`. Add `_stream_ollama()` for chat passthrough (returns `StreamingResponse` proxied directly by Open WebUI). Add `__event_emitter__` status ping for analytics path. Add `ollama_model` Valve used by all three paths.

**Tech Stack:** Python 3.12, httpx (async streaming), starlette `StreamingResponse`, pydantic `BaseModel`, Open WebUI Pipe API, pytest + unittest.mock

---

## File Map

| File | Change |
|---|---|
| `openwebui/filter_analytics.py` | Replace `Filter`→`Pipe`, `inlet`→`async pipe`, add `_stream_ollama`, add `ollama_model` Valve, remove `outlet` |
| `tests/test_filter_pipeline.py` | Add 3 new tests for pipe-specific behaviour |

All other files are unchanged — the underlying pipeline functions (`_run_supervisor`, `_run_query`, `_run_summarize`, etc.) are not touched.

---

## Task 1: Add `ollama_model` Valve and wire it through analytics agents

**Files:**
- Modify: `openwebui/filter_analytics.py`

The current `_ollama_chat()` takes `ollama_url` but the model is hardcoded to `OLLAMA_MODEL`. We need it to accept `model` as a parameter so the Valve value flows through.

- [ ] **Step 1: Update `_ollama_chat` signature to accept explicit model**

In `openwebui/filter_analytics.py`, find `_ollama_chat` (currently at line ~165) and change:

```python
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

This is unchanged — the signature already accepts `model`. Confirm it is already there.

- [ ] **Step 2: Add `ollama_model` to `Valves`**

Find the `Valves` class (currently at line ~324) and add `ollama_model`:

```python
class Valves(BaseModel):
    """Open WebUI admin-configurable settings for this pipe."""
    s3_bucket: str = S3_BUCKET
    aws_region: str = AWS_REGION
    ollama_url: str = OLLAMA_URL
    ollama_model: str = OLLAMA_MODEL
    enabled: bool = True
```

- [ ] **Step 3: Update `_run_supervisor` call sites to pass `ollama_model`**

`_run_supervisor` already accepts `ollama_url` as a parameter but the model is passed via `_ollama_chat`'s default. We need to thread `ollama_model` through.

Update `_run_supervisor` signature:

```python
def _run_supervisor(question: str, registry: dict, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> dict:
    """Returns {"table": str, "confidence": "high|low", "reasoning": str}."""
    registry_text = _registry_as_prompt(registry)
    messages = [
        {"role": "system", "content": _SUPERVISOR_SYSTEM},
        {"role": "user", "content": f"Available tables:\n{registry_text}\n\nQuestion: {question}"},
    ]
    raw = _ollama_chat(messages, model=ollama_model, ollama_url=ollama_url)
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

- [ ] **Step 4: Update `_run_query` to accept and pass `ollama_model`**

```python
def _run_query(question: str, table: str, registry: dict, s3_bucket: str, aws_region: str = AWS_REGION, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> dict:
    """Returns {"sql": str, "rows": list[dict], "capped": bool}."""
    schema = registry[table]
    if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", aws_region):
        raise ValueError(f"Invalid aws_region format: {aws_region!r}")
    col_text = ", ".join(f"{c['name']} ({c['type']})" for c in schema["columns"])
    messages = [
        {"role": "system", "content": _QUERY_SYSTEM},
        {"role": "user", "content": f"Table: {table}\nColumns: {col_text}\n\nQuestion: {question}"},
    ]
    raw = _ollama_chat(messages, model=ollama_model, ollama_url=ollama_url)
    sql = _strip_fences(raw)
    _validate_sql(sql, table, set(registry.keys()))

    import duckdb

    def _execute():
        conn = duckdb.connect()
        try:
            path = f"s3://{s3_bucket}/{table}/*.parquet"
            conn.execute("INSTALL httpfs; LOAD httpfs;")
            conn.execute(f"SET s3_region='{aws_region}';")
            conn.execute("SET s3_use_credential_chain=true;")
            conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
            return conn.execute(sql).fetchdf().to_dict(orient="records")
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_execute)
        try:
            rows = future.result(timeout=DUCKDB_TIMEOUT)
        except FuturesTimeoutError:
            raise TimeoutError(f"DuckDB query exceeded {DUCKDB_TIMEOUT}s")

    before_cap = len(rows)
    return {"sql": sql, "rows": rows[:ROW_CAP], "capped": before_cap > ROW_CAP}
```

- [ ] **Step 5: Update `_run_summarize` to accept and pass `ollama_model`**

```python
def _run_summarize(question: str, rows: list[dict], capped: bool, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> dict:
    """Returns {"summary": str, "chart_spec": dict|None}."""
    rows_json = json.dumps(rows[:50], default=str)
    if capped:
        cap_note = f" NOTE: results were capped at {ROW_CAP} rows; showing first 50 to model."
    elif len(rows) > 50:
        cap_note = f" NOTE: showing first 50 of {len(rows)} rows to model."
    else:
        cap_note = ""
    messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {"role": "user", "content": f"Question: {question}{cap_note}\n\nRows:\n{rows_json}"},
    ]
    raw = _ollama_chat(messages, model=ollama_model, ollama_url=ollama_url)
    parsed = json.loads(_strip_fences(raw).strip())
    summary = parsed.get("summary", "").strip()
    chart_spec = parsed.get("chart_spec")

    if chart_spec and rows:
        col_names = set(rows[0].keys())
        if (chart_spec.get("x") not in col_names or
                chart_spec.get("y") not in col_names or
                chart_spec.get("type") not in {"bar", "line", "pie", "table"}):
            chart_spec = None

    return {"summary": summary, "chart_spec": chart_spec}
```

- [ ] **Step 6: Update `_run_analytics` to accept and pass `ollama_model`**

```python
def _run_analytics(question: str, s3_bucket: str, aws_region: str = AWS_REGION, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> str:
    """Run full supervisor → query → summarize pipeline, return formatted response."""
    supervisor = _run_supervisor(question, REGISTRY, ollama_url, ollama_model)

    if supervisor["confidence"] == "low":
        return (
            "I wasn't confident which data to use for that question. "
            f"Could you be more specific? ({supervisor['reasoning']})"
        )

    table = supervisor["table"]
    query_result = _run_query(question, table, REGISTRY, s3_bucket, aws_region, ollama_url, ollama_model)
    rows = query_result["rows"]
    capped = query_result["capped"]

    if not rows:
        return "No data found for that query."

    summarize_result = _run_summarize(question, rows, capped, ollama_url, ollama_model)
    summary = summarize_result["summary"]
    chart_spec = summarize_result["chart_spec"]

    parts = [summary]
    if chart_spec:
        html = build_html_artifact(chart_spec, rows)
        if html:
            parts.append(html)

    return "\n\n".join(parts)
```

- [ ] **Step 7: Run existing tests — must all still pass**

```bash
ANALYTICS_S3_BUCKET=test ANALYTICS_AWS_REGION=ap-southeast-1 OLLAMA_BASE_URL=http://localhost:11434 \
  .venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -10
```

Expected: `82 passed`

- [ ] **Step 8: Commit**

```bash
git add openwebui/filter_analytics.py
git commit -m "feat: thread ollama_model through analytics agents, add to Valves"
```

---

## Task 2: Add `_stream_ollama` and replace `Filter` with `Pipe`

**Files:**
- Modify: `openwebui/filter_analytics.py`

This is the core structural change. We replace the `Filter` class and `inlet()` method with a `Pipe` class and `async pipe()`. We also add `_stream_ollama()` for the chat passthrough path.

- [ ] **Step 1: Add `StreamingResponse` import at the top of the file**

Find the imports block and add:

```python
from starlette.responses import StreamingResponse
```

The full imports block should be:

```python
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pydantic import BaseModel
from starlette.responses import StreamingResponse
from typing import Optional
import httpx
import json
import re
import traceback
```

- [ ] **Step 2: Add `_stream_ollama` function after `_ollama_chat`**

Add this function directly after `_ollama_chat` (before `REGISTRY`):

```python
async def _stream_ollama(messages: list[dict], ollama_url: str = OLLAMA_URL, model: str = OLLAMA_MODEL) -> StreamingResponse:
    """Stream Ollama response as SSE bytes. Returns StreamingResponse for Open WebUI to proxy directly."""
    async def generator():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                ollama_url,
                json={"model": model, "messages": messages, "stream": True},
                timeout=OLLAMA_TIMEOUT,
            ) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream")
```

- [ ] **Step 3: Replace `Filter` class with `Pipe` class**

Find the `Filter` class (starts with `class Filter:`) and replace the entire class with:

```python
class Pipe:
    def __init__(self):
        self.valves = Valves()

    async def pipe(self, body: dict, __event_emitter__=None) -> str | StreamingResponse:
        """Route message to analytics pipeline or Ollama passthrough based on intent."""
        if not self.valves.enabled:
            return await _stream_ollama(
                body.get("messages", []),
                self.valves.ollama_url,
                self.valves.ollama_model,
            )

        messages = body.get("messages", [])
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return await _stream_ollama(messages, self.valves.ollama_url, self.valves.ollama_model)

        question = user_messages[-1].get("content", "").strip()
        if not question:
            return await _stream_ollama(messages, self.valves.ollama_url, self.valves.ollama_model)

        intent = classify_intent(question)

        if intent == INTENT_CHAT:
            try:
                return await _stream_ollama(messages, self.valves.ollama_url, self.valves.ollama_model)
            except Exception as e:
                traceback.print_exc()
                return f"Chat service error: {e}"

        if intent == INTENT_AMBIGUOUS:
            return (
                "That sounds data-related — do you want me to run an analytics "
                "query on the NYC taxi dataset? If so, please describe what you'd "
                "like to know (e.g. 'show monthly revenue trend' or 'top boroughs by trips')."
            )

        # INTENT_ANALYTICS
        try:
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": False}})

            result = _run_analytics(
                question,
                self.valves.s3_bucket,
                self.valves.aws_region,
                self.valves.ollama_url,
                self.valves.ollama_model,
            )

            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": True}})

            return result
        except Exception as e:
            traceback.print_exc()
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": True}})
            return f"Analytics pipeline error: {e}"
```

- [ ] **Step 4: Remove the `outlet` method and the old `_run_analytics` placement**

The `outlet` method from the old `Filter` class must be gone. Verify it is not present anywhere in the file:

```bash
grep -n "def outlet" openwebui/filter_analytics.py
```

Expected: no output.

Also verify `_run_analytics` is still a module-level function (not inside any class):

```bash
grep -n "def _run_analytics\|class Filter\|class Pipe" openwebui/filter_analytics.py
```

Expected:
```
<line>:def _run_analytics(...)
<line>:class Pipe:
```

- [ ] **Step 5: Update module-level comment on `valves = Valves()`**

The comment references `Filter.valves` — update it to reflect `Pipe`:

```python
# Module-level instance required for Open WebUI's Valves schema discovery
# (hasattr check). Open WebUI mutates Pipe.valves directly when admin saves
# changes — pipe() reads self.valves, so UI changes take effect correctly.
valves = Valves()
```

- [ ] **Step 6: Run existing tests — must all still pass**

```bash
ANALYTICS_S3_BUCKET=test ANALYTICS_AWS_REGION=ap-southeast-1 OLLAMA_BASE_URL=http://localhost:11434 \
  .venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -10
```

Expected: `82 passed`

- [ ] **Step 7: Commit**

```bash
git add openwebui/filter_analytics.py
git commit -m "feat: replace Filter with Pipe, add _stream_ollama passthrough, async pipe() with event_emitter"
```

---

## Task 3: Add pipe-specific tests

**Files:**
- Modify: `tests/test_filter_pipeline.py`

Three new tests covering pipe-specific behaviour that the existing tests don't cover.

- [ ] **Step 1: Write the three failing tests**

Add to the end of `tests/test_filter_pipeline.py`:

```python
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from starlette.responses import StreamingResponse


def test_pipe_chat_returns_streaming_response():
    """_stream_ollama returns a StreamingResponse wrapping the Ollama byte stream."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
    from filter_analytics import _stream_ollama

    async def run():
        async def fake_aiter_bytes():
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'

        mock_response = MagicMock()
        mock_response.aiter_bytes = fake_aiter_bytes
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("filter_analytics.httpx.AsyncClient", return_value=mock_client):
            result = await _stream_ollama(
                [{"role": "user", "content": "hello"}],
                "http://ollama/v1/chat/completions",
                "qwen2.5-coder:7b",
            )
        assert isinstance(result, StreamingResponse)
        assert result.media_type == "text/event-stream"

    asyncio.run(run())


def test_pipe_analytics_emits_status_events():
    """pipe() emits done=False before pipeline and done=True after."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
    from filter_analytics import Pipe

    emitted = []

    async def fake_emitter(event):
        emitted.append(event)

    async def run():
        pipe = Pipe()
        pipe.valves.s3_bucket = "test-bucket"
        pipe.valves.aws_region = "ap-southeast-1"
        pipe.valves.ollama_url = "http://ollama/v1/chat/completions"
        pipe.valves.ollama_model = "qwen2.5-coder:7b"

        with patch("filter_analytics._run_analytics", return_value="summary text"):
            result = await pipe.pipe(
                {"messages": [{"role": "user", "content": "show monthly revenue trend"}]},
                __event_emitter__=fake_emitter,
            )

        assert result == "summary text"
        assert len(emitted) == 2
        assert emitted[0] == {"type": "status", "data": {"description": "Analyzing", "done": False}}
        assert emitted[1] == {"type": "status", "data": {"description": "Analyzing", "done": True}}

    asyncio.run(run())


def test_pipe_analytics_skips_emitter_when_none():
    """pipe() with __event_emitter__=None does not crash on analytics path."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "openwebui"))
    from filter_analytics import Pipe

    async def run():
        pipe = Pipe()
        pipe.valves.s3_bucket = "test-bucket"
        pipe.valves.aws_region = "ap-southeast-1"
        pipe.valves.ollama_url = "http://ollama/v1/chat/completions"
        pipe.valves.ollama_model = "qwen2.5-coder:7b"

        with patch("filter_analytics._run_analytics", return_value="summary text"):
            result = await pipe.pipe(
                {"messages": [{"role": "user", "content": "show monthly revenue trend"}]},
                __event_emitter__=None,
            )

        assert result == "summary text"

    asyncio.run(run())
```

- [ ] **Step 2: Run the three new tests — must FAIL (red)**

```bash
ANALYTICS_S3_BUCKET=test ANALYTICS_AWS_REGION=ap-southeast-1 OLLAMA_BASE_URL=http://localhost:11434 \
  .venv/bin/python -m pytest tests/test_filter_pipeline.py::test_pipe_chat_returns_streaming_response \
    tests/test_filter_pipeline.py::test_pipe_analytics_emits_status_events \
    tests/test_filter_pipeline.py::test_pipe_analytics_skips_emitter_when_none \
    -v --tb=short
```

Expected: 3 FAILED (Task 2 not done yet — if running tasks in order, Task 2 is already done and these should PASS)

- [ ] **Step 3: Run full test suite — all 85 tests must pass**

```bash
ANALYTICS_S3_BUCKET=test ANALYTICS_AWS_REGION=ap-southeast-1 OLLAMA_BASE_URL=http://localhost:11434 \
  .venv/bin/python -m pytest tests/ -v --tb=short 2>&1 | tail -10
```

Expected: `85 passed`

- [ ] **Step 4: Commit**

```bash
git add tests/test_filter_pipeline.py
git commit -m "test: add pipe-specific tests (streaming response, event_emitter, None guard)"
```

---

## Task 4: Smoke test in Open WebUI

**Files:** none — manual verification only

- [ ] **Step 1: Load updated filter_analytics.py in Open WebUI admin panel**

Go to **Admin Panel → Functions**. Find the existing filter function. Replace its content with the full content of `openwebui/filter_analytics.py`. Save.

Open WebUI will detect `class Pipe` and re-register it as a Pipe model automatically.

- [ ] **Step 2: Set Valves**

In the function's Valves settings:
- `s3_bucket`: `llmops-analytics-492372116094`
- `aws_region`: `ap-southeast-1`
- `ollama_url`: `http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions`
- `ollama_model`: `qwen2.5-coder:7b`
- `enabled`: `true`

- [ ] **Step 3: Smoke test 1 — chat passthrough**

Send: `explain what a linked list is`

Expected: streamed LLM response character-by-character, no analytics. Ollama's qwen2.5-coder:7b answers normally.

- [ ] **Step 4: Smoke test 2 — ambiguous**

Send: `taxi`

Expected: one-shot clarification message — "That sounds data-related — do you want me to run an analytics query..."

No LLM overwrite. Message appears immediately as a complete response.

- [ ] **Step 5: Smoke test 3 — analytics**

Send: `show monthly revenue trend`

Expected:
1. "Analyzing" spinner appears
2. Spinner dismisses
3. Summary text + Vega-Lite chart artifact rendered

- [ ] **Step 6: Commit final state**

```bash
git add openwebui/filter_analytics.py
git commit -m "feat: pipe rewrite complete — routed Pipe with streaming passthrough and analytics loading indicator"
```
