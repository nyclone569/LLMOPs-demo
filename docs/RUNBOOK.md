# LLMOps Platform — Operator Runbook

Mục đích: hướng dẫn vận hành, xử lý sự cố và quy trình thường ngày cho platform LLMOps trên EKS.

- Cluster: `llmops-cluster` — region `ap-southeast-1` — account `492372116094`
- GitOps: ArgoCD (sync tự động từ `main`)
- Secrets: AWS Secrets Manager `llmops/apikeys`, `llmops/supabase`

---

## 1. Truy cập & công cụ tiên quyết

```bash
# Lấy kubeconfig
aws eks update-kubeconfig --region ap-southeast-1 --name llmops-cluster

# Smoke test
kubectl get nodes
kubectl get applications -n argocd

# Cổng ArgoCD UI
kubectl get ingress -n argocd
```

Yêu cầu local: `kubectl >= 1.29`, `helm >= 3.13`, `aws-cli v2`, IAM role có quyền `eks:DescribeCluster` + RBAC `system:masters` qua `aws-auth` ConfigMap.

---

## 2. Health check toàn platform (chạy hằng ngày)

```bash
# Tất cả ArgoCD apps phải Synced + Healthy
kubectl get applications -n argocd -o wide

# Pod không Running phải = 0
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded

# HPA không có replica = 0
kubectl get hpa -A

# Alert đang firing
kubectl exec -n monitoring sts/alertmanager-kube-prometheus-stack-alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 alert query
```

Dashboards (qua Grafana ALB):
- `LLMOps Overview` — request rate, error rate, latency p50/p95/p99 per model
- `Cost Analysis` — spend per team/model, projection 30d
- `Analytics` — token usage, fallback rate, cache hit ratio
- `LLMOps Log Explorer` — Loki-backed live tail

---

## 3. Sự cố thường gặp

### 3.1 Open WebUI 502 / không vào được

```bash
kubectl -n open-webui get pods
kubectl -n open-webui logs deploy/open-webui --tail=200
```

Nguyên nhân thường gặp:
- ALB target group unhealthy → check `/health` trả 200.
- Startup quá lâu → kéo dài `startupProbe.failureThreshold` trong `open-webui-values.yaml`.
- Mất kết nối LiteLLM → check service `litellm.litellm.svc.cluster.local:4000` và NetworkPolicy.

### 3.2 LiteLLM HPA runaway / pod evicted

Triệu chứng: HPA scale lên trần, node `MemoryPressure`.

```bash
kubectl top pods -n litellm
kubectl describe nodes | grep -A5 Allocated
```

Khắc phục:
1. Kiểm tra leak: `kubectl exec -n litellm <pod> -- curl localhost:4000/metrics | grep memory`.
2. Tăng `resources.requests.memory` (đã pin `1100Mi`); cap `autoscaling.maxReplicas`.
3. Cooldown bằng `kubectl rollout restart deploy/litellm -n litellm`.

### 3.3 Langfuse trace không lên

```bash
kubectl -n langfuse logs deploy/langfuse-worker --tail=200 | grep -Ei 'error|s3|clickhouse'
```

Checklist:
- ClickHouse pod Ready? `kubectl -n langfuse get pod -l app.kubernetes.io/name=clickhouse`
- Worker thấy `CLICKHOUSE_CLUSTER_ENABLED=false`?
- Credential S3 đúng (cả `LANGFUSE_S3_*` lẫn `AWS_*`)?
- `LANGFUSE_HOST` ở LiteLLM = `http://langfuse-web.langfuse.svc.cluster.local:3000`.

### 3.4 ClickHouse migrate hang

Nguyên nhân: Keeper init chưa xong hoặc bật `ON CLUSTER` ngoài single-node.

```bash
kubectl -n langfuse logs langfuse-clickhouse-0 -c clickhouse
kubectl -n langfuse exec langfuse-clickhouse-0 -- clickhouse-client \
  --query="SELECT * FROM system.zookeeper WHERE path='/'"
```

Fix: đảm bảo env `CLICKHOUSE_CLUSTER_ENABLED=false` được set trong `langfuse-values.yaml`, restart worker.

### 3.5 ArgoCD app `OutOfSync` không tự sync

```bash
argocd app get <name>          # xem sync result
argocd app sync <name> --prune  # force sync (chỉ khi đã review diff)
kubectl -n argocd logs deploy/argocd-application-controller --tail=200
```

Nếu kẹt vì server-side apply conflict → xoá annotation tranh chấp hoặc `argocd app sync --replace`.

### 3.6 Loki query timeout / log không thấy

```bash
kubectl -n loki logs sts/loki-loki --tail=200
kubectl -n loki get pods
```

- Promtail chạy đủ DaemonSet trên mọi node?
  `kubectl -n loki get pods -l app.kubernetes.io/name=promtail -o wide`
- Path glob phải khớp containerd EKS (`/var/log/pods/*/*/*.log`).
- Compactor enabled để hết hạn 14d (xem `loki.yaml`).

---

## 4. Quy trình thay đổi & deploy

### 4.1 Deploy thay đổi thường

1. Branch from `main`, sửa Helm values hoặc manifest.
2. `git push` → ArgoCD auto-sync (selfHeal + prune).
3. Verify trên Grafana + `argocd app get <name>`.

### 4.2 Rollback nhanh

```bash
git revert <bad-commit> && git push
# hoặc
argocd app rollback <name> <revision>
```

### 4.3 Bật/tắt traffic-simulator

Nằm ở `argocd/apps/traffic-simulator.yaml`. Suspend bằng:
```bash
argocd app set traffic-simulator --sync-policy none
```

---

## 5. Bí mật & xoay key

### 5.1 Cấu trúc secret AWS SM

`llmops/apikeys` (JSON) — bắt buộc:
```
LITELLM_MASTER_KEY
LITELLM_SALT_KEY
WEBUI_SECRET_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
GEMINI_API_KEY
LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY
LANGFUSE_S3_ACCESS_KEY_ID
LANGFUSE_S3_SECRET_ACCESS_KEY
REDIS_PASSWORD
POSTGRESQL_PASSWORD
CLICKHOUSE_PASSWORD
```

Tuỳ chọn (SSO/OIDC — nếu chưa có, Open WebUI vẫn chạy local-auth):
```
OIDC_PROVIDER_URL          # ví dụ https://accounts.google.com/.well-known/openid-configuration
OIDC_CLIENT_ID
OIDC_CLIENT_SECRET
```

### 5.2 Xoay key — drill chuẩn

Đã verify bằng `WEBUI_SECRET_KEY` rotation drill (2026-06-05): SM update → ESO refresh < 30s → StatefulSet rollout 2/2 Ready ~1.5 phút.

1. Tạo key mới ở provider (vd. OpenAI dashboard) hoặc generate locally:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. Cập nhật value trong AWS SM (giữ nguyên các key khác):
   ```bash
   aws secretsmanager get-secret-value --region ap-southeast-1 \
     --secret-id llmops/apikeys --query SecretString --output text > /tmp/sm.json
   # ... edit /tmp/sm.json ...
   aws secretsmanager put-secret-value --region ap-southeast-1 \
     --secret-id llmops/apikeys --secret-string file:///tmp/sm.json
   rm /tmp/sm.json
   ```
3. ExternalSecrets refresh mỗi 1h. Force ngay:
   ```bash
   kubectl annotate externalsecret -n open-webui llmops-apikeys-secret \
     force-sync=$(date +%s) --overwrite
   kubectl annotate externalsecret -n litellm llmops-apikeys-secret \
     force-sync=$(date +%s) --overwrite
   kubectl annotate externalsecret -n langfuse llmops-apikeys-secret \
     force-sync=$(date +%s) --overwrite
   ```
4. Verify ESO đã sync xong:
   ```bash
   kubectl -n open-webui get externalsecret llmops-apikeys-secret \
     -o jsonpath='{.status.refreshTime}'
   ```
5. Rolling restart consumer để pickup env mới:
   ```bash
   kubectl rollout restart statefulset/open-webui -n open-webui
   kubectl rollout restart deploy/litellm -n litellm
   kubectl rollout restart deploy/langfuse-web -n langfuse
   kubectl rollout restart deploy/langfuse-worker -n langfuse
   ```
6. `kubectl rollout status` cho từng workload trước khi revoke key cũ ở provider.

### 5.3 Bật SSO/OIDC — Google Workspace

Helm values `open-webui-values.yaml` đã hard-code `OPENID_PROVIDER_URL` của Google. Chỉ cần thêm 2 key vào AWS SM.

1. **Google Cloud Console** → APIs & Services → Credentials → **Create OAuth client ID**:
   - Application type: **Web application**
   - Name: `LLMOps Internal Chat`
   - Authorized redirect URIs: `http://internal-llmops-open-webui-54615089.ap-southeast-1.elb.amazonaws.com/oauth/oidc/callback`
     (đổi sang `https://chat.<domain>/oauth/oidc/callback` khi có custom domain + ACM)
2. Copy `Client ID` và `Client secret`.
3. Thêm 2 key vào AWS SM `llmops/apikeys`:
   ```bash
   aws secretsmanager get-secret-value --region ap-southeast-1 \
     --secret-id llmops/apikeys --query SecretString --output text > /tmp/sm.json
   python3 -c "import json; d=json.load(open('/tmp/sm.json')); \
     d['OIDC_CLIENT_ID']='<paste>'; d['OIDC_CLIENT_SECRET']='<paste>'; \
     json.dump(d, open('/tmp/sm.json','w'))"
   aws secretsmanager put-secret-value --region ap-southeast-1 \
     --secret-id llmops/apikeys --secret-string file:///tmp/sm.json
   rm /tmp/sm.json
   ```
4. Force ExternalSecret refresh + rollout:
   ```bash
   kubectl annotate externalsecret -n open-webui llmops-apikeys-secret \
     force-sync=$(date +%s) --overwrite
   kubectl rollout restart statefulset/open-webui -n open-webui
   ```
5. Verify: vào ALB DNS, login form hiện **Sign in with Google**.

Restrict theo domain (tuỳ chọn): set env `OAUTH_ALLOWED_DOMAINS=company.com` trong helm values.

---

## 6. Quản trị chi phí

- Budget toàn platform: `$6000 / 30d` (LiteLLM `general_settings.max_budget`).
- Budget per team & model allowlist: PostSync Job `argocd/rbac-setup` (gọi LiteLLM admin API).
- Theo dõi spend: dashboard *Cost Analysis* + alert `LLMTeamBudgetExceeded`, `LLMPlatformBudgetForecast`.
- Khi alert budget firing: tạm thời tăng giới hạn qua admin API, mở ticket review usage.

---

## 7. Backup & DR

| Asset | Mechanism | RPO |
|---|---|---|
| PostgreSQL | gp3 EBS snapshot daily (terraform-managed) | 24h |
| Langfuse S3 traces | S3 versioning bật trên `llmops-langfuse-492372116094` | minutes |
| ClickHouse | snapshot EBS volume (single node) | 24h |
| ArgoCD config | Git là single source of truth | 0 |

Restore PostgreSQL:
```bash
# Snapshot → volume → patch PVC → roll PG primary
aws ec2 create-volume --snapshot-id <id> --availability-zone <az> --volume-type gp3
```

---

## 8. Liên hệ on-call

- Platform owner: nghiatd (`nyclone002@gmail.com`)
- Escalation: ArgoCD + Slack alert channel (cấu hình Slack webhook trong Alertmanager khi sẵn sàng).

---

## 9. Checklist trước khi handover

- [ ] Tất cả ArgoCD app `Synced + Healthy` ≥ 24h.
- [ ] Không alert P1 firing trong 7d gần nhất.
- [ ] Secret rotation drill chạy thành công ít nhất 1 lần (mục 5.2).
- [ ] Loki retention thực sự xoá log > 14d (kiểm tra `loki_compactor_*` metrics).
- [ ] Langfuse trace TTL 30d áp dụng trên project (UI → Settings → Data Retention).
- [ ] Dashboard Grafana + alert export `.json` lưu trong repo.
- [ ] Runbook này được tester ngoài đội đọc và phản hồi.
