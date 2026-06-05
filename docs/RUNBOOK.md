# LLMOps Platform — Operator Runbook

Mục đích: hướng dẫn vận hành, xử lý sự cố và quy trình thường ngày cho platform LLMOps trên EKS.

- Cluster: `llmops-cluster` — region `ap-southeast-1` — account `492372116094`
- GitOps: ArgoCD (sync tự động từ `main`)
- Secrets: AWS Secrets Manager `llmops/apikeys` (chính), `llmops/supabase` (legacy — không còn dùng sau khi LiteLLM bỏ `LITELLM_DB_URL`)

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

### 3.7 LiteLLM P1000 "Authentication failed" sau bootstrap fresh

Triệu chứng: pod LiteLLM CrashLoop, log có `prisma db error … Authentication failed against database server`. Nguyên nhân: Bitnami PostgreSQL regenerate password mới khi PVC mới, nhưng `LITELLM_DB_URL` ở SM còn cứng password cũ.

Đã fix permanently: `litellm-values.yaml` build `DATABASE_URL` từ env `POSTGRESQL_PASSWORD`. Nếu vẫn gặp:
1. Verify cùng password: `kubectl -n postgresql exec postgresql-primary-0 -- bash -c 'PGPASSWORD=$POSTGRESQL_PASSWORD psql -U postgres -c "SELECT 1"'`
2. Rotate `POSTGRESQL_PASSWORD` ở AWS SM, force-sync ESO ở cả 3 namespace (postgresql, litellm, langfuse), rồi rollout `postgresql-primary` lẫn `litellm`.

### 3.8 Langfuse migration P3009 deadlock

Triệu chứng: pod langfuse-web CrashLoop, log Prisma báo `Error: P3009` + `deadlock detected … ExclusiveLock on advisory lock`. Nguyên nhân: 2 replica langfuse-web chạy `CREATE INDEX CONCURRENTLY` cùng lúc (migration `20240104210051_add_model_indices`).

Recovery (đã verify 2026-06-05):
```bash
# 1. Scale 2 deployment về 0
kubectl -n langfuse scale deploy/langfuse-web --replicas=0
kubectl -n langfuse scale deploy/langfuse-worker --replicas=0

# 2. Đợi pod terminate xong, xoá row migration thất bại
kubectl -n postgresql exec postgresql-primary-0 -- \
  bash -c 'PGPASSWORD=$POSTGRESQL_PASSWORD psql -U postgres langfuse \
  -c "DELETE FROM _prisma_migrations WHERE finished_at IS NULL"'

# 3. Patch HPA min về 1 (nếu không sẽ tự scale 2 → deadlock lại)
kubectl -n langfuse patch hpa langfuse-web --type merge \
  -p '{"spec":{"minReplicas":1}}'

# 4. Scale 1 replica, đợi Ready
kubectl -n langfuse scale deploy/langfuse-web --replicas=1
kubectl -n langfuse rollout status deploy/langfuse-web

# 5. Khôi phục HPA min=2
kubectl -n langfuse patch hpa langfuse-web --type merge \
  -p '{"spec":{"minReplicas":2}}'
kubectl -n langfuse scale deploy/langfuse-worker --replicas=2
```

### 3.9 OIDC login không hiện nút Google

Triệu chứng: refresh ALB Open WebUI vẫn chỉ thấy form email/password. `kubectl exec ... curl localhost:8080/api/config` trả `"oauth":{"providers":{}}` rỗng.

Checklist:
1. Env `OAUTH_CLIENT_ID` có trong pod chưa? `kubectl -n open-webui exec open-webui-0 -- env | grep OAUTH`. Nếu thiếu → key sai tên trong SM.
2. Key trong K8s secret: `kubectl -n open-webui get secret llmops-apikeys-secret -o jsonpath='{.data}' | python3 -c "import sys,json; print(sorted(json.load(sys.stdin).keys()))"` — phải có `OIDC_CLIENT_ID` và `OIDC_CLIENT_SECRET`.
3. **Bẫy hay gặp**: paste credential vào tab "Key/value" của AWS Console làm nhầm credential thành KEY name → fix bằng cách put-secret-value qua CLI với JSON đúng cấu trúc (RUNBOOK §5.3).

### 3.10 kubectl báo "no such host" sau khi recreate cluster

Triệu chứng: `kubectl get nodes` báo `dial tcp: lookup <hash>.eks.amazonaws.com on 127.0.0.53:53: no such host`. EKS cluster bị recreate → endpoint hash mới, kubeconfig cũ trỏ stale.

Fix: `aws eks update-kubeconfig --name llmops-cluster --region ap-southeast-1`.

### 3.11 Redis / Langfuse / Postgres down (degraded mode test)

Yêu cầu §8.1 Requirements:

| Failure | Hành vi mong đợi | Cách test |
|---|---|---|
| Redis down | LiteLLM chat vẫn chạy, mất cache | `kubectl -n redis scale sts/redis-master --replicas=0`; thử chat → vẫn trả, latency tăng |
| Langfuse down | Chat vẫn chạy, trace mất | `kubectl -n langfuse scale deploy/langfuse-web --replicas=0`; chat tiếp tục được |
| LiteLLM 1 pod crash | API vẫn chạy | `kubectl -n litellm delete pod <pod>`; còn 2 replica phục vụ |
| Open WebUI 1 pod crash | UI vẫn chạy | `kubectl -n open-webui delete pod open-webui-0`; còn 1 replica |
| Postgres connection > 80% | Alert `PostgresConnectionHigh` fire | Chạy load thử, xem Grafana panel; alert phải fire trong 5 phút |

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
OIDC_CLIENT_ID             # Google OAuth client ID
OIDC_CLIENT_SECRET         # Google OAuth client secret
```

> `OPENID_PROVIDER_URL` đã hard-code trong `open-webui-values.yaml` (Google discovery), không cần để ở SM.
> `LITELLM_DB_URL` đã bỏ — LiteLLM build `DATABASE_URL` inline từ `POSTGRESQL_PASSWORD` để tránh drift.

**Lưu ý khi update SM qua Console UI**: dùng tab "Plaintext" thay vì tab "Key/value". Nếu thêm key qua "Key/value", AWS Console dễ paste credential nhầm thành KEY name → ESO sync xong env vẫn rỗng. Đã gặp với OIDC trong drill 2026-06-05.

### 5.2 Xoay key — drill chuẩn

Đã verify bằng `WEBUI_SECRET_KEY` rotation drill (2026-06-05): SM update → ESO refresh < 30s → StatefulSet rollout 2/2 Ready ~1.5 phút.

1. Tạo key mới ở provider (vd. OpenAI dashboard) hoặc generate locally:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. Update SM theo luồng chuẩn §5.4 (thay value của key cần rotate).
3. `kubectl rollout status` cho từng workload trước khi revoke key cũ ở provider.

### 5.3 Bật SSO/OIDC — Google Workspace

Helm values `open-webui-values.yaml` đã hard-code `OPENID_PROVIDER_URL` của Google. Chỉ cần thêm 2 key vào AWS SM.

1. **Google Cloud Console** → APIs & Services → Credentials → **Create OAuth client ID**:
   - Application type: **Web application**
   - Name: `LLMOps Internal Chat`
   - Authorized redirect URIs: `http://internal-llmops-open-webui-54615089.ap-southeast-1.elb.amazonaws.com/oauth/oidc/callback`
     (đổi sang `https://chat.<domain>/oauth/oidc/callback` khi có custom domain + ACM)
2. Copy `Client ID` và `Client secret`.
3. Thêm 2 key vào AWS SM theo luồng §5.4, edit step như sau:
   ```bash
   python3 -c "
   import json
   d = json.load(open('/tmp/sm.json'))
   d['OIDC_CLIENT_ID'] = '<paste client_id>'
   d['OIDC_CLIENT_SECRET'] = '<paste client_secret>'
   json.dump(d, open('/tmp/sm.json','w'))
   "
   ```
4. Verify: vào ALB DNS (qua VPN) hoặc port-forward + localhost, login form hiện **Sign in with Google**.

Restrict theo domain (tuỳ chọn): set env `OAUTH_ALLOWED_DOMAINS=company.com` trong helm values.

### 5.4 Cập nhật AWS Secrets Manager — luồng chuẩn

Quy trình áp dụng cho mọi thay đổi `llmops/apikeys`: thêm key mới, sửa value, xoay key, recover key bị mất. Tuân thủ để tránh 5 bẫy ở mục 5.5.

**Nguyên tắc**: `put-secret-value` thay thế **toàn bộ** payload, không merge — luôn đọc trước, edit, push lại toàn bộ JSON.

#### Bước 1 — Đọc payload hiện tại + verify

```bash
aws secretsmanager get-secret-value --region ap-southeast-1 \
  --secret-id llmops/apikeys --query SecretString --output text > /tmp/sm.json

python3 -c "
import json
d = json.load(open('/tmp/sm.json'))
print('Current keys:', sorted(d.keys()))
print('Total:', len(d))
"
```

#### Bước 2 — Edit JSON

```bash
# Cách A — Python one-liner cho 1 thay đổi
python3 -c "
import json
d = json.load(open('/tmp/sm.json'))
d['NEW_KEY'] = 'new_value'        # thêm/sửa
# del d['OLD_KEY']                # xoá
json.dump(d, open('/tmp/sm.json','w'))
"

# Cách B — editor cho nhiều thay đổi
vim /tmp/sm.json
```

#### Bước 3 — Verify bắt buộc trước khi push

```bash
python3 -c "
import json
d = json.load(open('/tmp/sm.json'))
required = ['LITELLM_MASTER_KEY','LITELLM_SALT_KEY','WEBUI_SECRET_KEY',
            'OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY',
            'LANGFUSE_PUBLIC_KEY','LANGFUSE_SECRET_KEY',
            'LANGFUSE_S3_ACCESS_KEY_ID','LANGFUSE_S3_SECRET_ACCESS_KEY',
            'REDIS_PASSWORD','POSTGRESQL_PASSWORD','CLICKHOUSE_PASSWORD']
missing = [k for k in required if k not in d]
print('Missing required:', missing)
assert not missing, 'STOP — required keys missing, do not push'
print('OK, safe to push. Keys:', sorted(d.keys()))
"
```

Nếu thấy `Missing required` → DỪNG, sửa lại file. Đừng push.

#### Bước 4 — Push + force-sync ESO + rollout

```bash
# 1. Push lên SM
aws secretsmanager put-secret-value --region ap-southeast-1 \
  --secret-id llmops/apikeys --secret-string file:///tmp/sm.json \
  --query VersionId --output text

# 2. Xoá temp file ngay
rm /tmp/sm.json

# 3. Force-sync ESO ở MỌI namespace dùng secret (loop)
for ns in $(kubectl get externalsecret -A \
  -o jsonpath='{range .items[?(@.metadata.name=="llmops-apikeys-secret")]}{.metadata.namespace}{"\n"}{end}'); do
  kubectl annotate externalsecret -n $ns llmops-apikeys-secret \
    force-sync=$(date +%s) --overwrite
done

# 4. Đợi K8s secret cập nhật (nếu thêm key mới, check key đó)
until kubectl -n open-webui get secret llmops-apikeys-secret \
  -o jsonpath='{.data.NEW_KEY}' | grep -q .; do
  sleep 3
done

# 5. Rollout consumer
kubectl -n open-webui rollout restart statefulset/open-webui
kubectl -n litellm   rollout restart deploy/litellm
kubectl -n langfuse  rollout restart deploy/langfuse-web
kubectl -n langfuse  rollout restart deploy/langfuse-worker

# 6. Verify env trong pod
kubectl -n open-webui exec open-webui-0 -- env | grep NEW_KEY
```

#### Recovery — Khi lỡ overwrite mất key

Đã dùng để khôi phục `OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET` 2026-06-05.

```bash
# 1. List versions
aws secretsmanager list-secret-version-ids --region ap-southeast-1 \
  --secret-id llmops/apikeys --include-deprecated \
  --query 'Versions[*].{VersionId:VersionId,Stages:VersionStages,Created:CreatedDate}' \
  --output table

# 2. Lấy version AWSPREVIOUS (chứa key đã mất)
PREV_ID=$(aws secretsmanager list-secret-version-ids --region ap-southeast-1 \
  --secret-id llmops/apikeys --include-deprecated \
  --query 'Versions[?contains(VersionStages,`AWSPREVIOUS`)].VersionId' \
  --output text)

aws secretsmanager get-secret-value --region ap-southeast-1 \
  --secret-id llmops/apikeys --version-id $PREV_ID \
  --query SecretString --output text > /tmp/prev.json

aws secretsmanager get-secret-value --region ap-southeast-1 \
  --secret-id llmops/apikeys \
  --query SecretString --output text > /tmp/curr.json

# 3. Merge key thiếu từ prev → curr
python3 -c "
import json
prev = json.load(open('/tmp/prev.json'))
curr = json.load(open('/tmp/curr.json'))
recover = ['OIDC_CLIENT_ID','OIDC_CLIENT_SECRET']   # đổi danh sách theo nhu cầu
for k in recover:
    if k in prev and k not in curr:
        curr[k] = prev[k]
        print(f'Restored {k}')
json.dump(curr, open('/tmp/curr.json','w'))
"

# 4. Push merged + force-sync + rollout (lặp lại Bước 4 ở trên)
aws secretsmanager put-secret-value --region ap-southeast-1 \
  --secret-id llmops/apikeys --secret-string file:///tmp/curr.json

rm /tmp/prev.json /tmp/curr.json
```

### 5.5 Bẫy hay gặp khi update SM

| Bẫy | Triệu chứng | Phòng tránh |
|---|---|---|
| Paste credential thành KEY name qua Console UI Key/value tab | ESO sync OK nhưng env trong pod thiếu biến (`grep OAUTH_CLIENT_ID` rỗng) | Luôn dùng CLI `put-secret-value` với JSON; nếu phải dùng Console thì chọn tab **Plaintext** không phải **Key/value** |
| `put-secret-value` thay thế toàn bộ payload | Key cũ biến mất sau update (vd. lỡ tay nhập 2 key OIDC làm mất WEBUI_SECRET_KEY) | Luôn `get-secret-value` trước, edit JSON đầy đủ, rồi mới push |
| Quên force-sync ESO | Pod restart vẫn dùng env cũ vì ESO chưa pull (chờ refresh interval 1h) | Loop annotate `force-sync=$(date +%s)` ở mọi namespace có ExternalSecret |
| Rollout sau khi K8s secret chưa cập nhật | Pod mới vẫn đọc value cũ | Đợi `kubectl get secret ... | grep <new key>` xuất hiện rồi mới rollout |
| Lưu `/tmp/sm.json` lâu | Credential lộ trên đĩa, log shell history | `rm` ngay sau khi push; xoá history nếu có lệnh chứa value |

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
- Escalation: ArgoCD UI + Alertmanager log (Slack webhook deferred — chưa cấu hình).

---

## 9. RBAC & teams

5 team đã setup qua `argocd/rbac-setup` Job (PostSync hook gọi LiteLLM admin API):

| Team | Allowlist | Budget/30d |
|---|---|---|
| engineering | coding-assistant, fast-chat, long-context | $100 |
| support | fast-chat | $40 |
| product | fast-chat | $30 |
| operations | fast-chat | $20 |
| executives | fast-chat, long-context | $10 |

Verify:
```bash
kubectl -n litellm exec deploy/litellm -- curl -s \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://localhost:4000/team/list | python3 -m json.tool
```

Tạo virtual key cho user mới (gán team):
```bash
curl -X POST http://litellm.litellm.svc/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"team_id":"engineering","user_id":"alice","max_budget":10}'
```

---

## 10. SLO verification

| SLO | Mục tiêu | Cách check |
|---|---|---|
| Open WebUI availability | 99.5% | Grafana panel `up{namespace="open-webui"}` sum trên 30d |
| LiteLLM availability | 99.9% | Panel `up{namespace="litellm"}` sum trên 30d |
| LiteLLM P95 latency | < 3s | `histogram_quantile(0.95, rate(litellm_request_duration_seconds_bucket[5m]))` |
| LLM request success rate | > 98% | `1 - rate(litellm_errors_total[5m]) / rate(litellm_requests_total[5m])` |
| Trace ingestion delay | < 60s | Langfuse worker queue depth dashboard |
| Alert detection time | < 5min | Alertmanager `firing_time - alert_start_time` |

Nếu một SLO miss > 24h → mở incident, post-mortem, ghi nhận vào `docs/weekly/`.

---

## 11. Checklist trước khi handover

- [x] Tất cả ArgoCD app `Synced + Healthy` ≥ 24h (đạt 2026-06-05 — Langfuse, LiteLLM, Open WebUI Healthy).
- [ ] Không alert P1 firing trong 7d gần nhất.
- [x] Secret rotation drill chạy thành công (`WEBUI_SECRET_KEY` rotated 2026-06-05, mục 5.2).
- [x] Loki retention 14d enforced bằng compactor (commit `b490273`).
- [x] ClickHouse 30d TTL áp dụng trên `traces/observations/scores` (verified `toIntervalDay(30)` 2026-06-05).
- [x] PII masking ở LiteLLM bật (`redact_user_api_key_info` + regex guardrail `pii-mask-pre-call`).
- [x] Dashboard Grafana + alert rules export trong `argocd/monitoring/`.
- [x] Runbook + 5 báo cáo tuần (`docs/weekly/week2..6.md`).
- [ ] Google OIDC bật (cần `OIDC_CLIENT_ID`/`OIDC_CLIENT_SECRET` đúng cấu trúc — mục 5.3 + 3.9).
- [ ] Demo deck cho stakeholder.
