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

6 stat panels across the full 24-column grid (w:4 each, h:4). All use `options.reduceOptions.calcs: ["lastNotNull"]` (Grafana 10.x path) so they always show current state regardless of the selected time range.

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
| 10 | Provider Success Rate | See query below | w:12, x:12 |

Provider Success Rate — uses `success_responses` directly to avoid division-by-zero when a provider has failures but no successes in the window:
```promql
sum(rate(litellm_deployment_success_responses_total[5m])) by (api_provider)
/
sum(rate(litellm_deployment_total_requests_total[5m])) by (api_provider)
```
`legendFormat: "{{api_provider}}"`, unit: `percentunit`.

### Row 3 — Latency (RED: Duration)

Two timeseries panels, h:8, y:20.

| # | Title | Query | gridPos |
|---|---|---|---|
| 11 | P50 / P95 Latency | P50 + P95 as two series on one panel | w:12, x:0 |
| 12 | Latency by Provider (P95) | P95 latency per `api_provider` | w:12, x:12 |

P50/P95 Latency queries (two targets, `legendFormat: "P{{quantile_desc}}"` or explicit "P50"/"P95"):
```promql
histogram_quantile(0.50, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))
histogram_quantile(0.95, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))
```

Latency by Provider query (replaces the misplaced rate panel — keeps the Duration row on-topic):
```promql
histogram_quantile(0.95, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le, api_provider))
```
`legendFormat: "{{api_provider}}"`, unit: `s`.

### Row 4 — Infrastructure (collapsible, collapsed by default)

y:28. Collapsed row panel wrapping all infra panels. Total row height: 8 (driven by the Pod CPU timeseries).

**Left column — stat/gauge panels (h:4 each, stacked at relative y:0 and y:4):**

| # | Title | Type | Query | gridPos |
|---|---|---|---|---|
| 13 | Open WebUI Ready Pods | stat | `sum(kube_deployment_status_replicas_ready{namespace="open-webui"})` | w:6, x:0, y:0 |
| 14 | Langfuse Ready Pods | stat | `sum(kube_deployment_status_replicas_ready{namespace="langfuse"})` | w:6, x:6, y:0 |
| 15 | Langfuse Queue Depth | stat | `sum(redis_key_size{key=~"bull:langfuse-new-events:.*"})` | w:6, x:0, y:4 — thresholds: 100=yellow, 500=red |
| 16 | PostgreSQL Conn Usage | gauge | `sum(pg_stat_activity_count) / sum(pg_settings_max_connections)` | w:6, x:6, y:4 — thresholds: 0.6=yellow, 0.8=red |

Note: panels 13–14 use `kube_deployment_status_replicas_ready` (deployment-level, aggregated by `sum`) rather than the pod-level `kube_pod_status_ready` used in the old dashboard. This gives a cleaner single number per service.

**Right column — timeseries (h:8, occupies full column height):**

| # | Title | Query | gridPos |
|---|---|---|---|
| 17 | Pod CPU Usage | `sum(rate(container_cpu_usage_seconds_total{namespace=~"litellm\|langfuse\|open-webui",container!=""}[5m])) by (namespace, pod)` | w:12, x:12, y:0 |

`container!=""` filter is required to exclude cAdvisor's aggregate pod rows and pause containers, which would otherwise double-count CPU usage.

---

## Panels Changed vs. Current Dashboard

| Panel | Change | Reason |
|---|---|---|
| Redis Memory Usage (gauge, panel 9) | Removed | Redundant — `RedisHighMemoryUsage` alert fires at 80%; health strip Firing Alerts covers it |
| Daily Token Usage stat (panel 7) | Moved to health strip (panel 6) | Summary only; full detail on future Cost tab |
| Daily Cost stat (panel 8) | Moved to health strip (panel 5) | Summary only; full detail on future Cost tab |
| Requests by Provider timeseries (panel 5) | Replaced by Latency by Provider (P95) in Row 3 | "Requests by Provider" is a Rate metric misplaced in the Duration row; replaced with the on-topic per-provider P95 latency panel |

Net panel count: 17 content panels + 1 row panel = 18 total (was 14). Increase: health strip adds 6 new stat panels; removing 3 panels and replacing 1 nets +3 content panels.

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
- Collapsible rows use Grafana panel `type: "row"` with `"collapsed": true` and child panels nested inside the row panel's own `panels` array (not the top-level `panels` array).
- The `ALERTS` metric is emitted by Prometheus for all PrometheusRule-managed alerts. **Known limitation:** Grafana-managed alert rules (stored in Grafana's database, not PrometheusRules) do NOT populate the `ALERTS` Prometheus metric — the Firing Alerts stat panel will not count those. Currently all platform alerts are PrometheusRules, so this is not an issue today.
- `or vector(0)` on the Firing Alerts query prevents the panel from going to "No data" state when no alerts are firing.
- Stat panel calc path in Grafana 10.x JSON: `options.reduceOptions.calcs: ["lastNotNull"]`.
- Threshold steps format — first step always has `value: null` (the base color). Example for Firing Alerts: `[{"color": "green", "value": null}, {"color": "red", "value": 1}]`. Example for Error Rate: `[{"color": "green", "value": null}, {"color": "yellow", "value": 0.01}, {"color": "red", "value": 0.05}]`.
- Daily Cost $: set `decimals: 2` to prevent auto-scaling to `$0` or excessive decimal places.
- Multi-series panels must set `legendFormat`: use `{{litellm_model_name}}` for Requests by Model, `{{api_provider}}` for Provider Success Rate and Latency by Provider.
- Panel IDs must be unique integers; renumber all panels sequentially during implementation.
