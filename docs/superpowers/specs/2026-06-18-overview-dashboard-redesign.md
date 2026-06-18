# LLMOps Platform Overview Dashboard Redesign

**Date:** 2026-06-18
**Status:** Approved

## Context

The current `llmops-overview` dashboard has 14 panels arranged without a clear structure: timeseries panels for traffic, errors, and latency are interleaved with stat panels for cost and pod counts. There is no health strip, no RED-method row grouping, and infrastructure panels sit alongside cost panels at the same visual weight. The dashboard targets a DevOps on-call engineer who needs to answer "is everything OK?" in under 5 seconds.

## Goals

- Provide immediate red/green health status at the top (no scrolling required)
- Organize remaining panels into RED-method rows (Rate → Errors → Duration)
- Collapse infrastructure details by default to reduce noise during normal operation
- Remove panels that duplicate alert coverage or belong on a dedicated tab
- Change default time range from `now-1h` to `now-3h`

## Non-Goals

- Cost/token breakdown panels — belong on a dedicated Cost tab (future work)
- State timeline / service health history row — deferred; requires alert state metric wiring
- Per-model or per-team drill-down rows — belong on the Analytics tab

---

## Design

### Row 0 — Health Strip (pinned, never collapsible)

6 stat panels across the full 24-column grid (w:4 each, h:4). All use `reducerOptions.calcs: ["lastNotNull"]` so they always show current state regardless of the selected time range.

| # | Title | Query | Unit | Thresholds |
|---|---|---|---|---|
| 1 | Firing Alerts | `count(ALERTS{alertstate="firing"}) or vector(0)` | short | 0=green, 1=red |
| 2 | Request Rate | `sum(rate(litellm_deployment_total_requests_total[5m]))` | reqps | — |
| 3 | Error Rate % | `sum(rate(litellm_deployment_failure_responses_total[5m])) / sum(rate(litellm_deployment_total_requests_total[5m]))` | percentunit | <0.01=green, 0.01–0.05=yellow, >0.05=red |
| 4 | P95 Latency | `histogram_quantile(0.95, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))` | s | <1=green, 1–3=yellow, >3=red |
| 5 | Daily Cost $ | `sum(increase(litellm_spend_metric_total[24h]))` | currencyUSD | — |
| 6 | Daily Tokens | `sum(increase(litellm_total_tokens_metric_total[24h]))` | short | — |

**gridPos:** y:0, panels at x:0, 4, 8, 12, 16, 20 respectively.

### Row 1 — Traffic (RED: Rate)

Two timeseries panels, h:8, y:4.

| # | Title | Query | gridPos |
|---|---|---|---|
| 7 | Request Rate (req/s) | `sum(rate(litellm_deployment_total_requests_total[5m]))` | w:12, x:0 |
| 8 | Requests by Model | `sum(rate(litellm_deployment_total_requests_total[5m])) by (litellm_model_name)` | w:12, x:12 |

### Row 2 — Errors (RED: Errors)

Two timeseries panels, h:8, y:12.

| # | Title | Query | gridPos |
|---|---|---|---|
| 9 | Error Rate | `sum(rate(litellm_deployment_failure_responses_total[5m])) / sum(rate(litellm_deployment_total_requests_total[5m]))` | w:12, x:0 |
| 10 | Provider Success Rate | `sum(rate(litellm_deployment_total_requests_total[5m])) by (api_provider)` minus failures, by `api_provider` | w:12, x:12 |

Provider Success Rate query:
```promql
1 - (
  sum(rate(litellm_deployment_failure_responses_total[5m])) by (api_provider)
  /
  sum(rate(litellm_deployment_total_requests_total[5m])) by (api_provider)
)
```

### Row 3 — Latency (RED: Duration)

Two timeseries panels, h:8, y:20.

| # | Title | Query | gridPos |
|---|---|---|---|
| 11 | P50 / P95 Latency | P50 + P95 as two series on one panel | w:12, x:0 |
| 12 | Requests by Provider | `sum(rate(litellm_deployment_total_requests_total[5m])) by (api_provider)` | w:12, x:12 |

P50/P95 Latency queries (two targets):
```promql
histogram_quantile(0.50, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))
```

### Row 4 — Infrastructure (collapsible, collapsed by default)

y:28. Collapsed row panel wrapping all infra panels.

**Stat panels (h:4):**

| # | Title | Query | gridPos |
|---|---|---|---|
| 13 | Open WebUI Ready Pods | `kube_deployment_status_replicas_ready{namespace="open-webui"}` | w:3, x:0 |
| 14 | Langfuse Ready Pods | `kube_deployment_status_replicas_ready{namespace="langfuse"}` | w:3, x:3 |
| 15 | Langfuse Queue Depth | `sum(redis_key_size{key=~"bull:langfuse-new-events:.*"})` | w:3, x:6 — thresholds: 100=yellow, 500=red |
| 16 | PostgreSQL Conn Usage | `sum(pg_stat_activity_count) / sum(pg_settings_max_connections)` | w:3, x:9 — gauge, thresholds: 0.6=yellow, 0.8=red |

**Timeseries (h:8):**

| # | Title | Query | gridPos |
|---|---|---|---|
| 17 | Pod CPU Usage | `sum(rate(container_cpu_usage_seconds_total{namespace=~"litellm|langfuse|open-webui"}[5m])) by (namespace, pod)` | w:12, x:12 |

---

## Panels Removed vs. Current Dashboard

| Panel | Reason |
|---|---|
| Redis Memory Usage (gauge, panel 9) | Redundant — `RedisHighMemoryUsage` alert fires at 80%; health strip Firing Alerts covers it |
| Daily Token Usage stat (panel 7) | Moved to health strip (panel 6) |
| Daily Cost stat (panel 8) | Moved to health strip (panel 5) |

Net panel count: 17 panels (was 14). Increase comes from the health strip adding 6 new stat panels, offset by removing 3 existing ones.

---

## Dashboard Settings

| Setting | Value |
|---|---|
| Default time range | `now-3h` to `now` |
| Auto-refresh | `30s` |
| Timezone | `browser` |

---

## Implementation Notes

- The entire dashboard is defined as a JSON blob inside `argocd/monitoring/grafana-dashboards.yaml` (ConfigMap key `llmops-overview.json`). Implementation rewrites that JSON in-place.
- Collapsible rows use Grafana panel `type: "row"` with `"collapsed": true` and child panels nested under it.
- The `ALERTS` metric is emitted by Prometheus and available in Grafana's default Prometheus datasource — no extra scrape config needed.
- `or vector(0)` on the Firing Alerts query prevents the panel from going to "No data" state when no alerts are firing.
- Panel IDs must be unique integers; renumber all panels sequentially during implementation.
