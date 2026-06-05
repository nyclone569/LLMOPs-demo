# Báo cáo tuần 5 — Dự án LLMOps Platform

**Tuần báo cáo:** Tuần 5 — kế hoạch: *Observability cho prompts/latency/cost/errors; logging, tracing, metrics; dashboards + alerts*

## 1. Công việc đã hoàn thành theo kế hoạch tuần 5

| Hạng mục kế hoạch | Trạng thái | Bằng chứng / Ghi chú |
|---|---|---|
| Observability cho **prompts** | Done | Mỗi prompt → trace Langfuse (user_id, model, tokens, cost, latency, parent/child span); ClickHouse là store backing |
| Observability cho **latency** | Done | Prometheus histogram `litellm_request_duration_seconds`; dashboard *Overview* hiển thị p50/p95/p99 per model |
| Observability cho **cost** | Done | LiteLLM spend tracking → PostgreSQL; dashboard *Cost Analysis* group by team/model + projection 30d |
| Observability cho **errors** | Done | `failure_callback: [langfuse, prometheus]`; counter `litellm_errors_total{model, error_type}`; alert `LLMHighErrorRate` |
| Structured logging | Done | `JSON_LOGS=true` ở mọi service (LiteLLM, Langfuse, Open WebUI); Promtail parse JSON tự động |
| Tracing | Done | Langfuse v3 (web + worker, 2 replicas mỗi loại); S3 trace events ở `llmops-langfuse-492372116094`; ClickHouse columnar store |
| Metrics infra | Done | kube-prometheus-stack: Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics |
| Metrics LLM workload | Done | ServiceMonitor cho `litellm`, `postgresql`, `redis`, `open-webui`; metric Langfuse qua `/api/public/metrics` |
| Dashboards | Done | 4 dashboard JSON commit trong repo: *LLMOps Overview*, *Cost Analysis*, *Analytics*, *LLMOps Log Explorer* (Loki) |
| Alerts | Done | 9 alert rule trong `argocd/monitoring/prometheus-rules.yaml`: service down, high error rate, high latency, budget warning/exceeded, fallback rate, pod crash, disk pressure, scrape failure |

## 2. Hạng mục vượt chỉ tiêu

**Logging stack riêng:**
- Loki SingleBinary (replication 1) với PVC gp3 30Gi.
- Promtail DaemonSet tự discover path containerd EKS (`/var/log/pods/*/*/*.log`).
- Loki gateway nginx + ALB internal cho external query.
- **Compactor** bật retention thực 14d (vừa thêm tuần này).

**Cost dashboard nâng cao:**
- Breakdown theo team alias + model alias + provider.
- 30-day cost projection dựa trên rate hiện tại.
- Cache hit ratio để chứng minh ROI của Redis cache.

**Alert hợp lý hoá:**
- Bỏ 2 phantom alert (`SensitivePromptDetected`, `LangfuseIngestionDelayHigh`) dùng metric không tồn tại.
- Alertmanager fix Slack URL invalid placeholder.

**Trace lưu dài hạn:**
- Trace event đẩy S3 → có thể replay vào ClickHouse khác.
- Worker scale HPA 2-3 replica.

## 3. Hạng mục chưa làm / còn nợ

| Hạng mục | Mức độ | Kế hoạch xử lý |
|---|---|---|
| ClickHouse TTL 30d cho trace cũ | P3 | Áp dụng `ALTER TABLE ... MODIFY TTL` hoặc cấu hình Langfuse data-retention ở project settings |
| Alertmanager → Slack webhook thật | P2 | Cần URL webhook từ workspace ops |
| Distributed tracing (OpenTelemetry) cho infra layer | P3 | Đề xuất phase 2 |
| SLO burn-rate alert | P3 | Cần định nghĩa SLI/SLO formal |
| Synthetic check (uptime probe ngoài cluster) | P3 | CloudWatch synthetic hoặc Prometheus blackbox |

## 4. Sự cố & bài học trong tuần (đã xử lý)

- Loki bundled Grafana dashboard không tương thích datasource → disable + tự viết log dashboard riêng (commit `dbfb357`, `f04e6e0`).
- Stale dashboard provider trong configmap gây Grafana xoá panel mới (commit `8a463cc`).
- Promtail không có trong chart Loki 5.x → tách ArgoCD app riêng (commit `9188153`).
- `kubeEtcd` ServiceMonitor lỗi vì EKS không expose etcd → disable (commit `ac3c7b3`).
- Loki dashboard query không khớp label `app=...` của Promtail → đổi sang `app_kubernetes_io_name`.

## 5. Kế hoạch tuần 6

Final hardening + handover:
1. End-to-end test: multi-provider failover, RBAC, budget enforcement, monitoring trigger.
2. Security review: secret rotation drill, NetworkPolicy audit, SSO/OIDC enable thật.
3. User acceptance test với 1-2 team thật.
4. Documentation: runbook, architecture diagram, handover guide, demo script.
5. Final demo cho stakeholder.
