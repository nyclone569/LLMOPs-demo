# Báo cáo tuần 3 — Dự án LLMOps Platform

**Tuần báo cáo:** Tuần 3 — kế hoạch: *Centralized LLM gateway, multi-provider routing, API key management, rate limiting, provider switching*

## 1. Công việc đã hoàn thành theo kế hoạch tuần 3

| Hạng mục kế hoạch | Trạng thái | Bằng chứng / Ghi chú |
|---|---|---|
| Centralized LLM gateway | Done | **LiteLLM proxy** deploy 3 replicas + HPA (min 3, max 4) trong namespace `litellm` |
| Hỗ trợ multiple model providers | Done | 4 provider: **OpenAI**, **Anthropic** (via `https://vip.digishop.work`), **Gemini**, **Ollama** (in-cluster llama3.2) |
| Provider routing | Done | Routing strategy `latency-based-routing`; logic load-balance giữa nhiều provider cho cùng 1 alias |
| Request/response handling | Done | OpenAI-compatible API ở `:4000`; ingress ALB internal `llmops-litellm` |
| API key management | Done | Tất cả key load qua env từ K8s Secret `llmops-apikeys-secret`; secret nguồn từ AWS Secrets Manager `llmops/apikeys` qua External Secrets Operator |
| Basic rate limiting | Done | Global `rpm: 1000`, `tpm: 1000000`; per-model `rpm` (coding-assistant 100, long-context 20, private-chat 10); `max_parallel_requests: 100` |
| Switch giữa các provider | Done | Model aliases (`fast-chat`, `coding-assistant`, `long-context`, `private-chat`, `claude-sonnet`, `claude-haiku`, ...); người dùng chọn alias trong Open WebUI |

## 2. Hạng mục vượt chỉ tiêu (đã làm sớm so với roadmap)

**Tính năng nâng cao (không nằm trong plan tuần 3):**
- **Fallback chain đa cấp**: mỗi alias → `claude-sonnet` → `private-chat/llama` (local) làm last resort khi cloud provider fail.
- **Budget enforcement**: platform-wide `$6000/30d` + per-team budget qua LiteLLM admin API (job `rbac-setup` PostSync).
- **RBAC theo team**: 5 team (engineering, support, product, operations, executives) với model allowlist riêng.
- **Redis cache**: prompt cache TTL 3600s, share Redis với Langfuse.
- **Observability callback**: `success_callback: [langfuse, prometheus]` cho mọi call.
- **Database tracking spend**: `DATABASE_URL` trỏ vào PostgreSQL để log per-key/per-team spend.
- **PodDisruptionBudget** + **NetworkPolicy** + **ServiceMonitor** cho LiteLLM.

**Vượt sang tuần 5 (Observability):**
- Prometheus metrics endpoint LiteLLM được scrape; ServiceMonitor đã định nghĩa.
- Langfuse trace cho mọi prompt/response.

**Vượt sang tuần 6 (Testing):**
- Traffic simulator Python (`argocd/traffic-simulator/simulator.py`) chạy burst, provider failure, sensitive prompts, expensive models để validate gateway.

## 3. Hạng mục chưa làm / còn nợ

| Hạng mục | Mức độ | Kế hoạch xử lý |
|---|---|---|
| Sensitive data masking ở gateway | P2 ✅ Done tuần 2 | Đã thêm regex guardrails `pii-mask-pre-call` + `redact_user_api_key_info` |
| Custom guardrail callback (Presidio/LLM-judge) | P3 | Đề xuất giai đoạn sau khi traffic ổn định |
| Streaming response benchmark | P3 | Cần đo throughput SSE end-to-end |

## 4. Sự cố & bài học trong tuần (đã xử lý)

- LiteLLM memory request 512Mi → 1500Mi để gỡ HPA runaway khi node 96-97% allocation.
- Rolling update deadlock: `maxUnavailable:1 maxSurge:0` để tránh kẹt khi node hết slot.
- NetworkPolicy DNS egress port 53 — fix lookup `langfuse-web.langfuse.svc`.
- Fallback chain: claude-sonnet làm cấp 1 vì Anthropic endpoint nội bộ có RPS cao hơn.

## 5. Kế hoạch tuần 4

Chuyển sang K8s packaging + GitOps:
1. Containerize toàn bộ service (sử dụng image upstream + Helm values).
2. ArgoCD App-of-Apps pattern + sync wave để bootstrap có thứ tự.
3. Terraform layer hoá: VPC → EKS → Bootstrap → App.
4. External Secrets + IRSA cho secret tự refresh.
5. Ingress ALB internal cho mọi UI/API.
