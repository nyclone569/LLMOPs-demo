# Open WebUI Filter Integration Design
**Date:** 2026-06-15
**Status:** Approved

## Overview

Integrate the NYC taxi analytics pipeline into Open WebUI's `private-chat` model using a **Filter** (Open WebUI Function). Users interact with `private-chat` as normal — the Filter intercepts each message, classifies intent, and either runs the analytics pipeline or passes the message through to the LLM unchanged. No new services, no Helm changes, no separate deployment.

---

## Architecture

### Components

| Component | Location | Role |
|---|---|---|
| `openwebui/filter_analytics.py` | repo, loaded via admin panel | Open WebUI Filter — intent router + pipeline caller |
| `analytics_agent/pipeline.py` | existing | Multi-agent pipeline (supervisor → query → summarize) |
| `schema_registry.json` | existing, mounted in pod | Table metadata fed to supervisor |
| Open WebUI Artifacts | built-in | Renders HTML chart artifacts in chat |

### How the Filter Hooks In

Open WebUI calls two methods on every registered Filter for a model:
- `inlet(body)` — before the message reaches the LLM. This is where routing and pipeline execution happen.
- `outlet(body)` — after the LLM responds. Passthrough (no-op) in this design.

The Filter is registered against the `private-chat` model in the Open WebUI admin panel. No changes to Helm values or ArgoCD manifests are needed.

---

## Intent Classification (Three-Tier Router)

### Signal Sets

**Domain terms** (strong signal — any one is meaningful):
`taxi`, `trip`, `fare`, `borough`, `zone`, `pickup`, `dropoff`, `vendor`, `route`, `revenue`, `passenger`, `yellow`, `green`, `fhv`, `manhattan`, `brooklyn`, `queens`, `bronx`, `staten island`

**Analytics words** (weak signal — only meaningful paired with a domain term):
`how many`, `average`, `total`, `compare`, `top`, `trend`, `count`, `per`, `rate`, `show`, `summary`, `breakdown`, `most`, `least`, `peak`, `weekly`, `monthly`, `daily`, `hourly`

### Routing Logic

```
domain_count >= 1 AND analytics_count >= 1  →  ANALYTICS
domain_count >= 1 AND analytics_count == 0  →  AMBIGUOUS
domain_count == 0                           →  CHAT
```

- **ANALYTICS**: run pipeline immediately, return result
- **AMBIGUOUS**: return clarification ask — "That sounds data-related. Want me to run an analytics query on that?"
- **CHAT**: return `body` unchanged, Open WebUI forwards to LLM

### Why Two-Tier Signals

Generic analytics words (`total`, `compare`) appear in normal chat and would cause false positives if used alone. Domain terms are narrow enough to the NYC taxi dataset that false positives are rare. Pairing them eliminates the ambiguity.

---

## Pipeline Integration

### Registry Loading

The Filter uses Open WebUI's `Valves` config mechanism to store the registry path. Loaded once at Filter startup via `load_registry()` from the existing `analytics_agent.registry` module. With Option A (registry bundled as dict), the valve is unused at runtime but kept for documentation. With Option B (ConfigMap mount), the valve path points to the mounted file inside the Open WebUI pod.

### Calling the Pipeline

The Filter inlines the three-agent logic directly (no `analytics_agent` import). LLM calls use `httpx` to the Ollama API. DuckDB is declared as a pip dependency in the Filter's `requirements` docstring.

Flow mirrors `analytics_agent/pipeline.py`:
1. Supervisor: select table from registry via Ollama
2. Query agent: generate + validate + execute SQL via DuckDB + S3/httpfs
3. Summarize agent: generate summary + chart_spec via Ollama

Result fields used:
- `summary` — markdown text, rendered as normal chat
- `chart_spec` — custom schema dict, converted to Vega-Lite and wrapped in HTML artifact
- `clarification` — returned as plain text (supervisor had low confidence)
- `error` — returned as user-friendly error message

### Two-Layer Clarification

There are two distinct clarification layers, each with a different job:

1. **Filter-level** (pre-pipeline): keyword router says AMBIGUOUS → ask user if they want analytics before touching the pipeline
2. **Pipeline-level** (inside pipeline): supervisor selects a table but returns `confidence: low` → asks for more specificity about the query itself

These are separate concerns and do not conflict.

---

## Response Formatting

### Text-only result

```
{summary text}
```

Rendered as normal chat markdown.

### Result with chart

```
{summary text}

<!DOCTYPE html>
<html>
<head>
  <script src="https://cdn.jsdelivr.net/npm/vega-embed@6"></script>
</head>
<body>
  <div id="chart"></div>
  <script>
    vegaEmbed('#chart', {vega_lite_spec});
  </script>
</body>
</html>
```

Open WebUI detects `<!DOCTYPE html>` and renders the HTML block as an interactive Artifact card in the chat message. The summary text appears above it as normal markdown.

### chart_spec Schema Conversion

The summarize agent returns a **custom schema**, not a native Vega-Lite spec:
```json
{"type": "bar", "x": "month", "y": "revenue", "series": []}
```

The Filter converts this to a Vega-Lite spec before building the HTML artifact:

| Custom type | Vega-Lite encoding |
|---|---|
| `bar` | `mark: "bar"`, x as ordinal, y as quantitative |
| `line` | `mark: "line"`, x as ordinal/temporal, y as quantitative |
| `pie` | `mark: "bar"` horizontal (simpler than `arc`, more readable for small datasets) |
| `table` | no chart — summary text only |

Multi-series charts (`series` field) are not supported in v1. `series` is always `[]` from the summarize agent and is ignored by the converter.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Pipeline raises `OllamaError` | Return: "Analytics service is unavailable. Try again shortly." |
| Pipeline raises `SupervisorError` / `QueryError` / `SummarizeError` | Return: "I couldn't answer that analytics question. Try rephrasing." |
| `result.error` set | Return user-friendly error text, log correlation_id |
| Filter itself crashes (unhandled exception) | Caught by top-level try/except in `inlet()` — return error text, never re-raise (crashing the filter degrades all of private-chat) |
| Registry file missing at startup | Log warning, set `self.registry = None`; `inlet()` skips analytics and routes everything to CHAT |

---

## Deployment

### Filter File Location

`openwebui/filter_analytics.py` — committed to repo for version control and review. Loaded into Open WebUI via **Admin Panel → Functions → Add Function** (paste content). No Docker image, no pod, no Helm change.

### Registry Access

The Filter runs inside the Open WebUI pod. It needs access to `schema_registry.json` and the `analytics_agent` package. Two options:

**Option A (simpler):** Bundle `schema_registry.json` content directly into the Filter as a Python dict constant. Zero file-path dependency.

**Option B (cleaner for updates):** Mount `schema_registry.json` as a ConfigMap volume in the Open WebUI pod, reference via valve path. Requires one Helm values change (`extraVolumes`, `extraVolumeMounts`).

**Recommended: Option A** for the initial implementation. The registry is 32 tables and fits in the Filter file. Switch to Option B if the registry grows or needs frequent updates without redeploying the Filter.

### analytics_agent Package

Open WebUI Functions run in a sandboxed Python environment. They can declare pip dependencies via a `requirements` docstring at load time, but cannot import from the host filesystem directly. Rather than packaging `analytics_agent` and managing a custom Open WebUI image, **the Filter is self-contained**:

- `classify_intent()` is inlined (pure Python, zero deps)
- LLM calls are made directly via `httpx` (already available in Open WebUI's environment) to the Ollama API at `http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions`
- DuckDB queries are executed via `duckdb` (declared in the Filter's `requirements` docstring — Open WebUI installs it at load time)
- The three-agent logic (supervisor → query → summarize) is inlined into the Filter, following the same patterns as `analytics_agent/` but without the import dependency

This keeps the Filter as a readable, standalone artifact — appropriate for a portfolio project. The `analytics_agent/` package remains the authoritative implementation for testing and the Streamlit dev tool.

**Schema registry** is bundled as a Python dict constant in the Filter file (Option A). No ConfigMap, no volume mount, no Helm changes.

---

## Files Changed / Created

| File | Change |
|---|---|
| `openwebui/filter_analytics.py` | **New** — self-contained Open WebUI Filter |
| `argocd/helm-values/open-webui-values.yaml` | **No change** — Filter is loaded via admin panel, no Helm needed |

---

## Testing

### Unit tests (`tests/test_filter_intent.py`)
- `test_routes_analytics_on_domain_plus_analytics_signal`
- `test_routes_ambiguous_on_domain_only`
- `test_routes_chat_on_no_domain_signal`
- `test_false_positive_total_without_domain` — "total cost of project" → CHAT
- `test_false_positive_compare_without_domain` — "compare these files" → CHAT
- `test_html_artifact_built_from_chart_spec`
- `test_no_html_when_no_chart_spec`

### Manual verification
- Load Filter in Open WebUI admin panel, hook to `private-chat`
- Send golden questions from `schema_registry.json` → verify chart + summary render
- Send normal chat message → verify LLM responds normally
- Send ambiguous message ("taxi stuff") → verify clarification ask appears
