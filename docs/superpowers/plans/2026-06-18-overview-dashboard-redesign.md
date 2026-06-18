# Overview Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `llmops-overview` Grafana dashboard JSON with a RED-method layout: health strip row → Traffic → Errors → Latency → collapsed Infrastructure.

**Architecture:** Single file change — rewrite the `llmops-overview.json` value inside `argocd/monitoring/grafana-dashboards.yaml`. ArgoCD auto-syncs the ConfigMap; Grafana sidecar picks it up within ~30s. No schema migrations, no new metrics — all queries already exist in the codebase.

**Tech Stack:** Grafana 10.x dashboard JSON, kube-prometheus-stack v56, ArgoCD GitOps, PromQL

---

### Task 1: Update dashboard settings and add health strip

Replace the current 14-panel flat layout with the new structure, starting with top-level settings and the health strip.

**Files:**
- Modify: `argocd/monitoring/grafana-dashboards.yaml` (lines 19–261, the entire `llmops-overview.json` value)

- [ ] **Step 1: Replace the overview dashboard JSON**

In `argocd/monitoring/grafana-dashboards.yaml`, replace the entire `llmops-overview.json: |` value (lines 10–261) with the new JSON below.

The new JSON has:
- `"time": {"from": "now-3h", "to": "now"}` (was `now-1h`)
- Health strip: 6 stat panels at y:0, h:4
- Traffic row: 2 timeseries at y:4, h:8
- Errors row: 2 timeseries at y:12, h:8
- Latency row: 2 timeseries at y:20, h:8
- Infra collapsed row at y:28: 4 stat/gauge panels + 1 timeseries

```yaml
  llmops-overview.json: |
    {
      "id": null,
      "uid": "llmops-overview",
      "title": "LLMOps Platform Overview",
      "tags": ["llmops", "overview"],
      "timezone": "browser",
      "schemaVersion": 38,
      "version": 2,
      "refresh": "30s",
      "time": {"from": "now-3h", "to": "now"},
      "panels": [
        {
          "id": 1,
          "title": "Firing Alerts",
          "type": "stat",
          "gridPos": {"h": 4, "w": 4, "x": 0, "y": 0},
          "targets": [
            {
              "expr": "count(ALERTS{alertstate=\"firing\"}) or vector(0)",
              "legendFormat": ""
            }
          ],
          "fieldConfig": {
            "defaults": {
              "unit": "short",
              "thresholds": {
                "mode": "absolute",
                "steps": [
                  {"color": "green", "value": null},
                  {"color": "red", "value": 1}
                ]
              }
            }
          },
          "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "background"
          }
        },
        {
          "id": 2,
          "title": "Request Rate",
          "type": "stat",
          "gridPos": {"h": 4, "w": 4, "x": 4, "y": 0},
          "targets": [
            {
              "expr": "sum(rate(litellm_deployment_total_requests_total[5m]))",
              "legendFormat": ""
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "reqps"}
          },
          "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]}
          }
        },
        {
          "id": 3,
          "title": "Error Rate",
          "type": "stat",
          "gridPos": {"h": 4, "w": 4, "x": 8, "y": 0},
          "targets": [
            {
              "expr": "sum(rate(litellm_deployment_failure_responses_total[5m])) / sum(rate(litellm_deployment_total_requests_total[5m]))",
              "legendFormat": ""
            }
          ],
          "fieldConfig": {
            "defaults": {
              "unit": "percentunit",
              "thresholds": {
                "mode": "absolute",
                "steps": [
                  {"color": "green", "value": null},
                  {"color": "yellow", "value": 0.01},
                  {"color": "red", "value": 0.05}
                ]
              }
            }
          },
          "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "background"
          }
        },
        {
          "id": 4,
          "title": "P95 Latency",
          "type": "stat",
          "gridPos": {"h": 4, "w": 4, "x": 12, "y": 0},
          "targets": [
            {
              "expr": "histogram_quantile(0.95, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))",
              "legendFormat": ""
            }
          ],
          "fieldConfig": {
            "defaults": {
              "unit": "s",
              "thresholds": {
                "mode": "absolute",
                "steps": [
                  {"color": "green", "value": null},
                  {"color": "yellow", "value": 1},
                  {"color": "red", "value": 3}
                ]
              }
            }
          },
          "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "background"
          }
        },
        {
          "id": 5,
          "title": "Daily Cost (USD)",
          "type": "stat",
          "gridPos": {"h": 4, "w": 4, "x": 16, "y": 0},
          "targets": [
            {
              "expr": "sum(increase(litellm_spend_metric_total[24h]))",
              "legendFormat": ""
            }
          ],
          "fieldConfig": {
            "defaults": {
              "unit": "currencyUSD",
              "decimals": 2
            }
          },
          "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]}
          }
        },
        {
          "id": 6,
          "title": "Daily Tokens",
          "type": "stat",
          "gridPos": {"h": 4, "w": 4, "x": 20, "y": 0},
          "targets": [
            {
              "expr": "sum(increase(litellm_total_tokens_metric_total[24h]))",
              "legendFormat": ""
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "short"}
          },
          "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]}
          }
        },
        {
          "id": 7,
          "title": "Request Rate (req/s)",
          "type": "timeseries",
          "gridPos": {"h": 8, "w": 12, "x": 0, "y": 4},
          "targets": [
            {
              "expr": "sum(rate(litellm_deployment_total_requests_total[5m]))",
              "legendFormat": "Total req/s"
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "reqps"}
          }
        },
        {
          "id": 8,
          "title": "Requests by Model",
          "type": "timeseries",
          "gridPos": {"h": 8, "w": 12, "x": 12, "y": 4},
          "targets": [
            {
              "expr": "sum(rate(litellm_deployment_total_requests_total[5m])) by (litellm_model_name)",
              "legendFormat": "{{ litellm_model_name }}"
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "reqps"}
          }
        },
        {
          "id": 9,
          "title": "Error Rate",
          "type": "timeseries",
          "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12},
          "targets": [
            {
              "expr": "sum(rate(litellm_deployment_failure_responses_total[5m])) / sum(rate(litellm_deployment_total_requests_total[5m]))",
              "legendFormat": "Error Rate"
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "percentunit", "min": 0, "max": 1}
          }
        },
        {
          "id": 10,
          "title": "Provider Success Rate",
          "type": "timeseries",
          "gridPos": {"h": 8, "w": 12, "x": 12, "y": 12},
          "targets": [
            {
              "expr": "sum(rate(litellm_deployment_success_responses_total[5m])) by (api_provider) / sum(rate(litellm_deployment_total_requests_total[5m])) by (api_provider)",
              "legendFormat": "{{ api_provider }}"
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "percentunit", "min": 0, "max": 1}
          }
        },
        {
          "id": 11,
          "title": "P50 / P95 Latency",
          "type": "timeseries",
          "gridPos": {"h": 8, "w": 12, "x": 0, "y": 20},
          "targets": [
            {
              "expr": "histogram_quantile(0.50, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))",
              "legendFormat": "P50"
            },
            {
              "expr": "histogram_quantile(0.95, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le))",
              "legendFormat": "P95"
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "s"}
          }
        },
        {
          "id": 12,
          "title": "Latency by Provider (P95)",
          "type": "timeseries",
          "gridPos": {"h": 8, "w": 12, "x": 12, "y": 20},
          "targets": [
            {
              "expr": "histogram_quantile(0.95, sum(rate(litellm_request_total_latency_metric_bucket[5m])) by (le, api_provider))",
              "legendFormat": "{{ api_provider }}"
            }
          ],
          "fieldConfig": {
            "defaults": {"unit": "s"}
          }
        },
        {
          "id": 13,
          "title": "Infrastructure",
          "type": "row",
          "gridPos": {"h": 1, "w": 24, "x": 0, "y": 28},
          "collapsed": true,
          "panels": [
            {
              "id": 14,
              "title": "Open WebUI Ready Pods",
              "type": "stat",
              "gridPos": {"h": 4, "w": 6, "x": 0, "y": 29},
              "targets": [
                {
                  "expr": "sum(kube_deployment_status_replicas_ready{namespace=\"open-webui\"})",
                  "legendFormat": ""
                }
              ],
              "fieldConfig": {
                "defaults": {
                  "unit": "short",
                  "thresholds": {
                    "mode": "absolute",
                    "steps": [
                      {"color": "red", "value": null},
                      {"color": "green", "value": 1}
                    ]
                  }
                }
              },
              "options": {
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "colorMode": "background"
              }
            },
            {
              "id": 15,
              "title": "Langfuse Ready Pods",
              "type": "stat",
              "gridPos": {"h": 4, "w": 6, "x": 6, "y": 29},
              "targets": [
                {
                  "expr": "sum(kube_deployment_status_replicas_ready{namespace=\"langfuse\"})",
                  "legendFormat": ""
                }
              ],
              "fieldConfig": {
                "defaults": {
                  "unit": "short",
                  "thresholds": {
                    "mode": "absolute",
                    "steps": [
                      {"color": "red", "value": null},
                      {"color": "green", "value": 1}
                    ]
                  }
                }
              },
              "options": {
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "colorMode": "background"
              }
            },
            {
              "id": 16,
              "title": "Langfuse Queue Depth",
              "type": "stat",
              "gridPos": {"h": 4, "w": 6, "x": 0, "y": 33},
              "targets": [
                {
                  "expr": "sum(redis_key_size{key=~\"bull:langfuse-new-events:.*\"})",
                  "legendFormat": ""
                }
              ],
              "fieldConfig": {
                "defaults": {
                  "unit": "short",
                  "thresholds": {
                    "mode": "absolute",
                    "steps": [
                      {"color": "green", "value": null},
                      {"color": "yellow", "value": 100},
                      {"color": "red", "value": 500}
                    ]
                  }
                }
              },
              "options": {
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "colorMode": "background"
              }
            },
            {
              "id": 17,
              "title": "PostgreSQL Connection Usage",
              "type": "gauge",
              "gridPos": {"h": 4, "w": 6, "x": 6, "y": 33},
              "targets": [
                {
                  "expr": "sum(pg_stat_activity_count) / sum(pg_settings_max_connections)",
                  "legendFormat": ""
                }
              ],
              "fieldConfig": {
                "defaults": {
                  "unit": "percentunit",
                  "min": 0,
                  "max": 1,
                  "thresholds": {
                    "mode": "absolute",
                    "steps": [
                      {"color": "green", "value": null},
                      {"color": "yellow", "value": 0.6},
                      {"color": "red", "value": 0.8}
                    ]
                  }
                }
              }
            },
            {
              "id": 18,
              "title": "Pod CPU Usage",
              "type": "timeseries",
              "gridPos": {"h": 8, "w": 12, "x": 12, "y": 29},
              "targets": [
                {
                  "expr": "sum(rate(container_cpu_usage_seconds_total{namespace=~\"litellm|langfuse|open-webui\",container!=\"\"}[5m])) by (namespace, pod)",
                  "legendFormat": "{{ namespace }}/{{ pod }}"
                }
              ],
              "fieldConfig": {
                "defaults": {"unit": "cores"}
              }
            }
          ]
        }
      ]
    }
```

- [ ] **Step 2: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('argocd/monitoring/grafana-dashboards.yaml'))" && echo "YAML valid"
```

Expected: `YAML valid`

- [ ] **Step 3: Verify the JSON inside the ConfigMap is valid**

```bash
python3 -c "
import yaml, json
cm = yaml.safe_load(open('argocd/monitoring/grafana-dashboards.yaml'))
data = cm['data']['llmops-overview.json']
d = json.loads(data)
print('panels:', len(d['panels']))
print('time:', d['time'])
print('version:', d['version'])
"
```

Expected output:
```
panels: 13
time: {'from': 'now-3h', 'to': 'now'}
version: 2
```

(13 = 12 content panels + 1 row panel)

- [ ] **Step 4: Spot-check panel structure**

```bash
python3 -c "
import yaml, json
cm = yaml.safe_load(open('argocd/monitoring/grafana-dashboards.yaml'))
d = json.loads(cm['data']['llmops-overview.json'])
for p in d['panels']:
    infra = ' (collapsed row)' if p['type'] == 'row' else ''
    children = f\" [{len(p.get('panels',[]))} children]\" if 'panels' in p else ''
    print(f\"  id={p['id']:2d} y={p['gridPos']['y']:2d} type={p['type']:12s} title={p['title']!r}{infra}{children}\")
"
```

Expected output (order by id):
```
  id= 1 y= 0 type=stat         title='Firing Alerts'
  id= 2 y= 0 type=stat         title='Request Rate'
  id= 3 y= 0 type=stat         title='Error Rate'
  id= 4 y= 0 type=stat         title='P95 Latency'
  id= 5 y= 0 type=stat         title='Daily Cost (USD)'
  id= 6 y= 0 type=stat         title='Daily Tokens'
  id= 7 y= 4 type=timeseries   title='Request Rate (req/s)'
  id= 8 y= 4 type=timeseries   title='Requests by Model'
  id= 9 y=12 type=timeseries   title='Error Rate'
  id=10 y=12 type=timeseries   title='Provider Success Rate'
  id=11 y=20 type=timeseries   title='P50 / P95 Latency'
  id=12 y=20 type=timeseries   title='Latency by Provider (P95)'
  id=13 y=28 type=row          title='Infrastructure' (collapsed row) [5 children]
```

- [ ] **Step 5: Commit**

```bash
git add argocd/monitoring/grafana-dashboards.yaml
git commit -m "feat: redesign overview dashboard with RED-method layout

- Add health strip: 6 stat panels (firing alerts, req rate, error %, p95 latency, daily cost, daily tokens)
- Reorganize panels into RED rows: Traffic / Errors / Latency
- Replace Requests-by-Provider with Latency-by-Provider (P95) in Duration row
- Collapse infrastructure panels into collapsible row (default: collapsed)
- Remove Redis Memory Usage (redundant with RedisHighMemoryUsage alert)
- Change default time range from now-1h to now-3h
- Use kube_deployment_status_replicas_ready for pod health stats"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task coverage |
|---|---|
| Health strip: 6 stat panels (firing alerts, req rate, error %, p95, daily cost, daily tokens) | Task 1 panels 1–6 ✓ |
| `options.reduceOptions.calcs: ["lastNotNull"]` on all stat panels | Task 1 — all stat panels include this ✓ |
| Traffic row: Request Rate + Requests by Model timeseries | Task 1 panels 7–8 ✓ |
| Errors row: Error Rate + Provider Success Rate (using success_responses) | Task 1 panels 9–10, query matches spec ✓ |
| Latency row: P50/P95 + Latency by Provider (P95) | Task 1 panels 11–12 ✓ |
| Infra row collapsed by default | Task 1 panel 13, `"collapsed": true` ✓ |
| Open WebUI + Langfuse Ready Pods using `kube_deployment_status_replicas_ready` | Task 1 panels 14–15 ✓ |
| Langfuse Queue Depth stat, thresholds 100/500 | Task 1 panel 16 ✓ |
| PostgreSQL Connection Usage gauge, thresholds 0.6/0.8 | Task 1 panel 17 ✓ |
| Pod CPU with `container!=""` filter | Task 1 panel 18 ✓ |
| Redis Memory Usage removed | Absent from new JSON ✓ |
| Default time range `now-3h` | Task 1, `"time": {"from": "now-3h", "to": "now"}` ✓ |
| `decimals: 2` on Daily Cost | Task 1 panel 5 ✓ |
| `legendFormat` on multi-series panels | Panels 8, 10, 12, 18 all have `{{ label }}` formats ✓ |

**Placeholder scan:** No TBDs, no "implement later", all queries spelled out. ✓

**Type consistency:** Single task, no cross-task type references. ✓

**gridPos y-coordinate check:**
- Health strip: y:0, h:4 → ends at y:4 ✓
- Traffic: y:4, h:8 → ends at y:12 ✓
- Errors: y:12, h:8 → ends at y:20 ✓
- Latency: y:20, h:8 → ends at y:28 ✓
- Row panel: y:28, h:1 ✓
- Infra children: y:29 (top stat row), y:33 (bottom stat row), y:29 (CPU timeseries, h:8 fills y:29–37) ✓
- Left column: w:6+w:6=12, x:0+x:6 ✓; right column: w:12, x:12 ✓

All checks pass.
