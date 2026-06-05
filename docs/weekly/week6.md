# Báo cáo tuần 6 — Dự án LLMOps Platform

**Tuần báo cáo:** Tuần 6 — kế hoạch: *E2E testing, security review, UAT, validate failover/RBAC/monitoring/GitOps, prepare docs + runbook + handover + demo*

## 1. Công việc đã hoàn thành theo kế hoạch tuần 6

| Hạng mục kế hoạch | Trạng thái | Bằng chứng / Ghi chú |
|---|---|---|
| End-to-end testing | Done | **Traffic simulator** Python (`argocd/traffic-simulator/simulator.py`) chạy 4 kịch bản: burst load, provider failure injection, sensitive prompts, expensive models; chạy như CronJob, hiện đã suspend sau khi validate |
| Multi-provider failover | Done | Fallback chain claude-sonnet → llama đã verify qua test thủ tắt key OpenAI: gateway tự fail-over, latency tăng 200ms, error rate không spike |
| Access control validation | Done | RBAC per-team via `argocd/rbac-setup/setup-teams.py` PostSync Job; verify token của team `support` không gọi được `coding-assistant` (403) |
| Monitoring validation | Done | Alert `LLMHighErrorRate` đã fire trong drill provider failure; budget alert `LLMTeamBudgetExceeded` fire khi simulator chạy expensive scenario |
| GitOps deployment validation | Done | Mọi thay đổi (115+ commit trên `main`) đều auto-sync qua ArgoCD; rollback thử qua `git revert` → app tự đồng bộ về trạng thái cũ |
| Documentation | Done | `docs/Requirements.md`, `docs/APPLY_PIPELINE.md`, `docs/GUIDE_FOR_BEGINNERS.md`, `docs/LEARNING_NOTES.md`, `docs/llmops-full-architecture.drawio`, `docs/RUNBOOK.md` (mới tuần này), `docs/weekly/week{1..6}.md` |
| Runbook | Done | `docs/RUNBOOK.md` — health check, incident response cho 6 sự cố thường gặp, secret rotation drill, backup/DR, handover checklist |
| Handover guide | Partial | `docs/GUIDE_FOR_BEGINNERS.md` + `RUNBOOK.md` đủ cho on-call; cần script demo cuối |
| Final demo | Pending | Cần stakeholder schedule |

## 2. Hạng mục vượt chỉ tiêu

- **Security hardening sâu**: regex PII masking guardrail (`pii-mask-pre-call`), `redact_user_api_key_info`, `redact_messages_in_exceptions` ở LiteLLM (đã thêm tuần này).
- **Retention thực thi**: Loki compactor + 14d (mới enable tuần này).
- **Cost guard rails 3 lớp**: per-user (LiteLLM virtual key), per-team (admin API), platform (`max_budget: $6000/30d`).
- **Auto-healing**: ArgoCD `selfHeal: true` + `prune: true` đảm bảo drift tự revert.
- **IRSA + External Secrets**: zero IAM user, secret tự refresh sau khi rotate AWS SM.

## 3. Hạng mục chưa làm / còn nợ

| Hạng mục | Mức độ | Trạng thái hiện tại |
|---|---|---|
| SSO/OIDC thật (Google Workspace / Azure AD) | P2 | Helm values đã sẵn block `extraEnvVars` OIDC `optional: true`; chờ 3 key OIDC_* đưa vào AWS SM + redirect URI thật của ALB |
| Langfuse `NEXTAUTH_URL` chỉnh sang ALB DNS thật | P3 | Chờ cluster mới bootstrap xong → lấy ALB DNS → update values |
| Secret rotation drill thực tế | P3 | Procedure đã viết trong runbook §5.2; cần chạy 1 lần để confirm |
| ClickHouse 30d TTL cho trace | P3 | Cần SQL `ALTER TABLE ... MODIFY TTL` hoặc dùng Langfuse project data-retention setting |
| Alertmanager → Slack thật | P2 | Cần URL webhook |
| ACM cert + custom domain | P2 | Hiện HTTP-only nội bộ |
| UAT với team thật | P2 | Cần 1-2 team engineering thử dùng 1 tuần |
| Demo deck cho stakeholder | P2 | Outline có sẵn (architecture diagram + runbook); cần slide |

## 4. Sự cố & bài học trong tuần (đã xử lý)

- Cluster EKS bị recreate (mất ArgoCD/CRD) → cần bootstrap lại; runbook đã có procedure update kubeconfig.
- Phát hiện retention Loki không enforce thật (chỉ TSDB index ages out, chunk file stay) → bật compactor.
- LiteLLM masking chỉ có ở level redact API-key info — bổ sung regex guardrail cho PII/secret.

## 5. Trạng thái tổng kết dự án

**Đã đạt 5/5 tiêu chí hoàn thành:**

| Tiêu chí | Trạng thái |
|---|---|
| Launch a ChatGPT-like internal UI for employees | ✅ Open WebUI 2 replicas + HPA |
| Launch a centralized LLM gateway for multiple model providers | ✅ LiteLLM 3 replicas, 4 provider, fallback chain |
| Observability for prompts, latency, cost, and errors | ✅ Langfuse + Prometheus + Grafana + Loki |
| Kubernetes-based deployment and GitOps-style operations | ✅ EKS + ArgoCD App-of-Apps + Terraform 4 layer |
| Monitoring, logging, and tracing for both infra and LLM workloads | ✅ kube-prometheus-stack + Loki + Langfuse |

## 6. Kế hoạch tiếp theo (post-tuần 6)

1. Đóng các P2 còn lại: SSO/OIDC, Slack webhook, ACM cert, demo deck.
2. Chạy secret rotation drill thật.
3. Onboard team thật làm UAT 1 tuần.
4. Final demo + sign-off stakeholder.
5. Đề xuất phase 2: Presidio guardrail, distributed tracing (OTel), synthetic monitoring, SLO burn-rate alerts.
