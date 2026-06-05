# Báo cáo tuần 4 — Dự án LLMOps Platform

**Tuần báo cáo:** Tuần 4 — kế hoạch: *Containerize, K8s manifests/Helm charts, env config, secrets, ingress, GitOps deploy flow*

## 1. Công việc đã hoàn thành theo kế hoạch tuần 4

| Hạng mục kế hoạch | Trạng thái | Bằng chứng / Ghi chú |
|---|---|---|
| Containerize UI + Gateway | Done | Dùng image upstream: `ghcr.io/open-webui/open-webui` (UI) và `ghcr.io/berriai/litellm-non_root:main-latest` (gateway) — không tự build, tránh maintenance overhead |
| Helm charts cho deployment | Done | Open WebUI chart v14.6.0, Langfuse v1.0.0, Bitnami PostgreSQL/Redis, LiteLLM dùng chart local `argocd/helm-values/litellm-chart/` |
| Kubernetes manifests | Done | `argocd/apps/` chứa 15 Application manifests, `argocd/manifests/` cho các resource phụ |
| Environment-based configuration | Done | Terraform `terraform.tfvars` per layer; Helm values riêng cho mỗi service; env vars load qua `valueFrom.secretKeyRef` |
| Secrets management | Done | **AWS Secrets Manager** (`llmops/apikeys`, `llmops/supabase`) → **External Secrets Operator** (IRSA) → K8s Secret `llmops-apikeys-secret` ở mỗi namespace; refresh interval 1h |
| Ingress routing | Done | **AWS Load Balancer Controller** + ALB internal cho từng service (`llmops-open-webui`, `llmops-litellm`, `llmops-langfuse`, `llmops-loki`, `llmops-grafana`, ArgoCD public) |
| GitOps deployment flow | Done | **ArgoCD App-of-Apps**: `root-app.yaml` quản lý mọi child Application; sync wave có thứ tự (storage → secrets → DB → services → monitoring); `automated: {prune, selfHeal}` |

## 2. Hạng mục vượt chỉ tiêu

**Hạ tầng IaC hoàn chỉnh:**
- 4 Terraform layer riêng biệt với remote state ở S3 `llmops-tfstate-492`:
  - `terraform/vpc` — VPC + subnets (3 AZ), NAT GW, IGW
  - `terraform/eks` — EKS cluster 1.29, managed node groups
  - `terraform/bootstrap` — ArgoCD, External Secrets, AWS LBC, EBS CSI, Cert Manager, Metrics Server, gp3 StorageClass, IRSA roles, GitHub OIDC cho CI
  - `terraform/app` — AWS resource ngoài K8s (Secrets Manager entries)
- State có dependency rõ ràng qua `terraform_remote_state`.

**Bảo mật chiều sâu:**
- **NetworkPolicy** mặc định deny-all + allow DNS egress + allow ingress giữa các namespace cần thiết.
- **PodSecurityContext** runAsNonRoot, drop capabilities ALL.
- **PodDisruptionBudget** cho mọi stateful workload.
- **IRSA** (IAM Roles for Service Accounts) thay vì IAM user cố định.

**GitOps maturity:**
- Sync wave: storage(2) → DB(3) → app(5) → monitoring(6) → rbac(7) → simulator(suspended) — bảo đảm thứ tự bootstrap.
- App-of-Apps thuận tiện onboard service mới: chỉ cần thêm 1 file YAML.

## 3. Hạng mục chưa làm / còn nợ

| Hạng mục | Mức độ | Kế hoạch xử lý |
|---|---|---|
| TLS termination ở ALB | P2 | Cần ACM cert + domain nội bộ; hiện đang HTTP-only nội bộ |
| Helm chart riêng cho LiteLLM (đã có local) công bố qua OCI repo | P3 | Optional — chỉ làm nếu muốn reuse |
| ArgoCD notifications → Slack | P3 | Bộ controller đã sẵn, chỉ thiếu webhook |
| CI pipeline (GitHub Actions) qua OIDC | P3 | terraform/bootstrap/github_oicd.tf đã sẵn trust policy; thiếu workflow file |

## 4. Sự cố & bài học trong tuần (đã xử lý)

- Stale endpoint kubeconfig sau khi cluster bị tái tạo → tài liệu hoá `aws eks update-kubeconfig` ở runbook.
- Bitnami `clickhouse` chart bị xoá khỏi Docker Hub → tách standalone deployment trong `argocd/manifests/clickhouse.yaml` thay vì subchart Langfuse.
- ServerSideApply conflict với CRD ArgoCD lớn → thêm `syncOptions: ServerSideApply=true`.
- ConfigMap không sync khi sửa: bật `argocd.argoproj.io/sync-options: Replace=true` cho LiteLLM config.

## 5. Kế hoạch tuần 5

Observability đầy đủ:
1. Prometheus + Grafana stack qua `kube-prometheus-stack`.
2. Loki + Promtail cho logs.
3. Langfuse cho LLM traces.
4. Dashboards: overview, cost, analytics, log explorer.
5. Alerting rules: budget, latency, error rate, fallback rate, service down.
