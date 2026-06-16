# Open WebUI Pipe Rewrite Design
**Date:** 2026-06-16
**Status:** Approved
**Supersedes:** `2026-06-15-openwebui-filter-integration-design.md`

## Overview

Rewrite `openwebui/filter_analytics.py` from a **Filter** to a **Pipe** (Open WebUI Function). The Pipe becomes a standalone model in the Open WebUI sidebar — one chat handles both general conversation (streamed via Ollama passthrough) and NYC taxi analytics (supervisor → query → summarize pipeline). No new services, no Helm changes, no separate deployment.

The root cause driving this change: Open WebUI's Filter `inlet()` cannot short-circuit the LLM call. After `inlet()` returns, `generate_chat_completion` is called unconditionally — injecting an assistant message and setting `stream=False` does not prevent Ollama from running and overwriting the result. A Pipe's `pipe()` method *is* the response; Ollama is only called when the Pipe explicitly calls it.

---

## Architecture

### Components

| Component | Location | Role |
|---|---|---|
| `openwebui/filter_analytics.py` | repo, loaded via admin panel | Open WebUI Pipe — intent router + pipeline caller |
| `analytics_agent/` | existing | Reference implementation + unit tests |
| Open WebUI Artifacts | built-in | Renders HTML chart artifacts in chat |

### How the Pipe Hooks In

Open WebUI detects `class Pipe` in the loaded module and registers it as a model entry (not a filter on an existing model). The Pipe appears in the model sidebar under whatever name is set in the admin panel (e.g. `private-chat`).

`pipe(body, __event_emitter__=None)` is the sole entry point. It receives the full request body and returns either a `str` (one-shot) or a `StreamingResponse` (streamed). Open WebUI routes the return type automatically.

The original Ollama `private-chat` model can be hidden from the sidebar — users interact only with the Pipe model.

---

## Intent Classification (Three-Tier Router)

Unchanged from the Filter design.

**Domain terms** (strong signal):
`taxi`, `trip`, `fare`, `borough`, `zone`, `pickup`, `dropoff`, `vendor`, `route`, `revenue`, `passenger`, `yellow`, `green`, `fhv`, `manhattan`, `brooklyn`, `queens`, `bronx`, `staten island`

**Analytics words** (weak signal — only meaningful paired with a domain term):
`how many`, `average`, `total`, `compare`, `top`, `trend`, `count`, `per`, `rate`, `show`, `summary`, `breakdown`, `most`, `least`, `peak`, `weekly`, `monthly`, `daily`, `hourly`

```
domain_count >= 1 AND analytics_count >= 1  →  ANALYTICS
domain_count >= 1 AND analytics_count == 0  →  AMBIGUOUS
domain_count == 0                           →  CHAT
```

---

## Data Flow

```
user message → pipe(body)
    │
    ├─ classify_intent(last user message)
    │
    ├─ INTENT_CHAT
    │   └─ _stream_ollama(messages, ollama_url, ollama_model)
    │       └─ httpx async stream → StreamingResponse → proxied to browser
    │
    ├─ INTENT_AMBIGUOUS
    │   └─ return clarification string (one-shot)
    │
    └─ INTENT_ANALYTICS
        ├─ __event_emitter__("Analyzing", done=False)   ← spinner shown
        ├─ _run_analytics(question, s3_bucket, aws_region, ollama_url, ollama_model)
        │   └─ _run_supervisor → _run_query → _run_summarize (unchanged)
        ├─ __event_emitter__("Analyzing", done=True)    ← spinner dismissed
        └─ return result string (one-shot)
```

### Streaming Passthrough (`_stream_ollama`)

Calls Ollama with `stream=True` via `httpx.AsyncClient`. Returns a `starlette.responses.StreamingResponse` wrapping the async byte iterator. Open WebUI detects `isinstance(res, StreamingResponse)` and proxies `body_iterator` directly — no chunk wrapping, no double SSE encoding.

```python
async def _stream_ollama(messages, ollama_url, model):
    async def generator():
        async with httpx.AsyncClient() as client:
            async with client.stream("POST", ollama_url,
                json={"model": model, "messages": messages, "stream": True},
                timeout=OLLAMA_TIMEOUT,
            ) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk
    return StreamingResponse(generator(), media_type="text/event-stream")
```

### Status Events (`__event_emitter__`)

Fires via Socket.IO independently of the response body. The spinner appears while `_run_analytics` is executing, dismissed when it returns.

```python
await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": False}})
result = _run_analytics(...)
await __event_emitter__({"type": "status", "data": {"description": "Analyzing", "done": True}})
```

`pipe()` must be `async def` to `await` the event emitter.

---

## Valves

```python
class Valves(BaseModel):
    s3_bucket: str = S3_BUCKET
    aws_region: str = AWS_REGION
    ollama_url: str = OLLAMA_URL
    ollama_model: str = OLLAMA_MODEL   # new — was hardcoded constant before
    enabled: bool = True
```

`ollama_model` is used by all three paths: analytics agents (`_ollama_chat`) and chat passthrough (`_stream_ollama`). One valve to change the model everywhere.

Module-level `valves = Valves()` is retained — Open WebUI requires it for Valves schema discovery on both Filter and Pipe types.

---

## What Changes vs. Current Filter

| | Current (Filter) | New (Pipe) |
|---|---|---|
| Class | `Filter` | `Pipe` |
| Entry method | `def inlet(body)` | `async def pipe(body, __event_emitter__=None)` |
| CHAT path | return body unchanged (LLM still called) | `_stream_ollama()` → `StreamingResponse` |
| ANALYTICS path | inject assistant msg + stream=False (broken) | return result string directly |
| AMBIGUOUS path | inject assistant msg + stream=False (broken) | return clarification string directly |
| Valves | s3_bucket, aws_region, ollama_url, enabled | + `ollama_model` |
| `outlet()` | no-op | removed |
| `_ollama_chat()` | blocking POST for agents | unchanged — used by analytics agents only |
| `_stream_ollama()` | does not exist | new — async httpx stream → StreamingResponse |
| Loading indicator | none | `__event_emitter__` status on analytics path |

**Unchanged:** `classify_intent`, `_run_supervisor`, `_run_query`, `_run_summarize`, `_validate_sql`, `chart_spec_to_vegalite`, `build_html_artifact`, `REGISTRY`, all prompt constants, `Valves` structure (minus `outlet`), module-level `valves = Valves()`.

---

## Two-Layer Clarification

Unchanged from Filter design. Two separate concerns:

1. **Pipe-level** (pre-pipeline): intent router returns AMBIGUOUS → ask user before touching the pipeline
2. **Pipeline-level** (inside pipeline): supervisor returns `confidence: low` → ask for more specificity

---

## Response Formatting

Unchanged from Filter design. Analytics results: summary text + optional `<!DOCTYPE html>` Vega-Lite artifact. Open WebUI renders the HTML block as an interactive Artifact card.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Analytics pipeline raises any exception | Caught by top-level try/except in `pipe()` — return user-friendly error string |
| `_stream_ollama()` raises (Ollama unreachable) | Caught by CHAT-path try/except — return error string (one-shot fallback) |
| `__event_emitter__` is None | Guarded: `if __event_emitter__:` — safe when called outside Open WebUI (tests) |

`pipe()` never re-raises. A crash returns a string, not a 500.

---

## Deployment

### Admin Panel

Load `openwebui/filter_analytics.py` via **Admin Panel → Functions → Add Function** (paste content). Set function type to **Pipe**. Open WebUI detects `class Pipe` automatically — no type selection needed.

Set Valves:
- `s3_bucket`: `llmops-analytics-492372116094`
- `aws_region`: `ap-southeast-1`
- `ollama_url`: `http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions`
- `ollama_model`: `qwen2.5-coder:7b`
- `enabled`: `true`

Hide the original Ollama `private-chat` model from the sidebar (Workspace → Models → toggle visibility). Users see only the Pipe model.

### No Infrastructure Changes

No Helm changes, no ArgoCD changes, no new pods. IRSA, S3, duckdb pre-install, and PYTHONPATH are all already in place from the Filter deployment.

---

## Files Changed

| File | Change |
|---|---|
| `openwebui/filter_analytics.py` | Rewrite: `Filter` → `Pipe`, `inlet` → `async pipe`, add `_stream_ollama`, add `ollama_model` Valve |
| `tests/test_filter_pipeline.py` | Update: rename Filter→Pipe references, add pipe-specific tests |

---

## Testing

### Existing tests (all pass unchanged)
All tests in `tests/` test the underlying functions directly — none depend on `Filter` or `inlet()`. They remain valid.

### New tests (`tests/test_filter_pipeline.py`)
- `test_pipe_chat_returns_streaming_response` — mock httpx, verify `_stream_ollama()` returns `StreamingResponse`
- `test_pipe_analytics_emits_status_events` — mock `__event_emitter__`, verify called with `done=False` then `done=True`
- `test_pipe_analytics_skips_emitter_when_none` — call `pipe()` with `__event_emitter__=None`, verify no crash

### Smoke test (manual)
1. Send `explain what a linked list is` → expect streamed LLM response (passthrough)
2. Send `taxi` → expect clarification ask (ambiguous, one-shot)
3. Send `show monthly revenue trend` → expect "Analyzing" spinner, then summary + chart artifact
