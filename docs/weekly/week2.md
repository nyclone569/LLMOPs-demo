# Báo cáo tuần 2 — Dự án LLMOps Platform

**Tuần báo cáo:** Tuần 2 — kế hoạch: *UI prototype + auth + conversation history + API design UI↔Gateway*

## 1. Công việc đã hoàn thành theo kế hoạch tuần 2

| Hạng mục kế hoạch | Trạng thái | Bằng chứng / Ghi chú |
|---|---|---|
| Internal UI prototype cho nhân viên | Done | Deploy **Open WebUI** trên namespace `open-webui`, 2 replicas, HPA min 2 |
| Basic chat functionality | Done | Chat UI hoạt động end-to-end qua LiteLLM gateway, hỗ trợ multi-model |
| User authentication | Done (cơ bản) | Auth nội bộ của Open WebUI đã bật; SSO/OIDC để slot tuần sau |
| Conversation history | Done | Lưu trong PostgreSQL (Bitnami chart, gp3 PVC) |
| API design UI ↔ LLM Gateway | Done | Open WebUI gọi LiteLLM qua OpenAI-compatible API; cấu hình endpoint & key qua External Secrets |

## 2. Hạng mục vượt chỉ tiêu (đã làm sớm so với roadmap)

Các phần dưới đây thuộc **tuần 3–5** trong kế hoạch nhưng đã hoàn thành trong tuần 2:

**Vượt sang tuần 3 — LLM Gateway:**
- LiteLLM deploy 3 pods + HPA, multi-provider routing (Anthropic qua endpoint nội bộ `https://vip.digishop.work`, Ollama llama3.2 self-host).
- Fallback chain: mọi model non-llama → `claude-sonnet` → `private-chat/llama`.
- API key management qua **AWS Secrets Manager** + External Secrets Operator (secret `llmops/apikeys`).
- Rate limiting & **budget per team**: 5 teams với model allowlist + monthly budget; budget toàn platform $6000/30d.

**Vượt sang tuần 4 — K8s + GitOps:**
- Toàn bộ stack đóng gói Helm + manifests, deploy qua **ArgoCD** (public ALB).
- Terraform cho VPC, EKS (`llmops-cluster`, ap-southeast-1), bootstrap, app.
- Secrets management qua AWS SM + ESO; ingress ALB; environment-based config.

**Vượt sang tuần 5 — Observability:**
- **Prometheus + Grafana**: 3 dashboard JSON (overview, cost-analysis, analytics) + dashboard Loki Log Explorer.
- **Alerting rules**: 9 alert rules trong `prometheus-rules.yaml`; Alertmanager đã sửa cấu hình.
- **Logging**: Loki + Promtail DaemonSet (đã fix path glob cho containerd EKS).
- **Tracing LLM**: Langfuse (web + worker, 2 replicas mỗi loại) với ClickHouse + S3 bucket `llmops-langfuse-492372116094`.
- **ServiceMonitor**: litellm, redis, postgresql, open-webui.

**Vượt sang tuần 6 — Testing:**
- Traffic simulator Python (burst, provider failure, sensitive prompts, expensive models) — hiện đã suspend sau khi dùng để validate.

## 3. Hạng mục chưa làm / còn nợ

| Hạng mục | Mức độ | Kế hoạch xử lý |
|---|---|---|
| **SSO/OIDC** integration thật | P2 | Cần OIDC client credentials đẩy vào AWS SM — slot tuần 3 |
| **Sensitive data masking** trong prompt logs | P2 | Cấu hình regex masking ở LiteLLM logging callback |
| Langfuse `NEXTAUTH_URL` còn trỏ `localhost` | P3 | Update sang ALB DNS sau khi confirm domain |
| **Retention policy**: 30d traces / 14d logs | P3 | Cấu hình Loki retention + Langfuse TTL ClickHouse |
| **Secret rotation test** procedural | P3 | Rotate key trong AWS SM, verify pod tự reload |
| Tài liệu **runbook + handover guide** | P2 | Đã có `APPLY_PIPELINE.md`, `GUIDE_FOR_BEGINNERS.md`, `LEARNING_NOTES.md`; cần runbook incident chính thức |

## 4. Sự cố & bài học trong tuần (đã xử lý)

Một số fix đáng chú ý đã commit trong tuần:
- Langfuse probe path schema v1.0.0 (`langfuse.web.livenessProbe`).
- ClickHouse migrations: `CLICKHOUSE_CLUSTER_ENABLED=false` cho single-node.
- Tách DB Langfuse và LiteLLM (`langfuse` vs `postgres`).
- NetworkPolicy DNS egress port 53 cho litellm/langfuse/open-webui.
- Right-size CPU/mem cho langfuse-worker & LiteLLM để gỡ HPA runaway và node 96–97% allocation.
- Gỡ phantom alerts (`SensitivePromptDetected`, `LangfuseIngestionDelayHigh`) dùng metric không tồn tại.
- Promtail tách thành ArgoCD app riêng (chart loki 5.x không còn subchart promtail).

## 5. Kế hoạch tuần 3

Vì backlog tuần 3–5 đã hoàn thành, tuần 3 sẽ refocus sang các gap P2:
1. SSO/OIDC thật (Google/Azure AD) cho Open WebUI + Langfuse + ArgoCD.
2. Sensitive data masking ở LiteLLM logging.
3. Retention policy Loki + ClickHouse.
4. Runbook + secret rotation drill.
5. Cập nhật `NEXTAUTH_URL` Langfuse + smoke test end-to-end.
