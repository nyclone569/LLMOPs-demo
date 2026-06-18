# LLMOps Platform - Operator Runbook

Purpose: operational guidance, incident response, and routine operating procedures for the LLMOps platform on EKS.

- Cluster: `llmops-cluster` - region `ap-southeast-1` - account `492372116094`
- GitOps: ArgoCD (auto-syncs from `main`)
- Secrets: AWS Secrets Manager `llmops/apikeys` (shared application keys)

---

## 1. Access and prerequisites

```bash
# Get kubeconfig
aws eks update-kubeconfig --region ap-southeast-1 --name llmops-cluster

# Smoke test
kubectl get nodes
kubectl get applications -n argocd

# ArgoCD UI ingress
kubectl get ingress -n argocd
```

Local requirements: `kubectl >= 1.29`, `helm >= 3.13`, `aws-cli v2`, and an IAM role with `eks:DescribeCluster` plus `system:masters` RBAC via the `aws-auth` ConfigMap.

---

## 2. Full platform health check (run daily)

```bash
# All ArgoCD apps should be Synced + Healthy
kubectl get applications -n argocd -o wide

# Non-running pods should be 0
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded

# Check HPA state
kubectl get hpa -A

# Alerts currently firing
kubectl exec -n monitoring sts/alertmanager-kube-prometheus-stack-alertmanager -- \
  amtool --alertmanager.url=http://localhost:9093 alert query
```

Dashboards (via Grafana ALB):
- `LLMOps Overview` - request rate, error rate, latency p50/p95/p99 per model
- `Cost Analysis` - spend per team/model, 30-day projection
- `Analytics` - token usage, fallback rate, cache hit ratio
- `LLMOps Log Explorer` - Loki-backed live tail

---

## 3. Common incidents

### 3.1 Open WebUI returns 502 / cannot be reached

```bash
kubectl -n open-webui get pods
kubectl -n open-webui logs deploy/open-webui --tail=200
```

Common causes:
- ALB target group unhealthy -> verify `/health` returns 200.
- Startup is too slow -> increase `startupProbe.failureThreshold` in `open-webui-values.yaml`.
- Lost connection to LiteLLM -> check service `litellm.litellm.svc.cluster.local:4000` and NetworkPolicy.

### 3.2 LiteLLM HPA runaway / pod evicted

Symptoms: HPA scales to the limit, node reports `MemoryPressure`.

```bash
kubectl top pods -n litellm
kubectl describe nodes | grep -A5 Allocated
```

Mitigation:
1. Check for leaks: `kubectl exec -n litellm <pod> -- curl localhost:4000/metrics | grep memory`.
2. Increase `resources.requests.memory` (currently pinned at `1100Mi`); cap `autoscaling.maxReplicas`.
3. Cool down with `kubectl rollout restart deploy/litellm -n litellm`.

### 3.3 Langfuse traces do not appear

```bash
kubectl -n langfuse logs deploy/langfuse-worker --tail=200 | grep -Ei 'error|s3|clickhouse'
```

Checklist:
- Is the ClickHouse pod Ready? `kubectl -n langfuse get pod -l app.kubernetes.io/name=clickhouse`
- Does the worker see `CLICKHOUSE_CLUSTER_ENABLED=false`?
- Are S3 credentials correct (`LANGFUSE_S3_*` and `AWS_*`)?
- Is `LANGFUSE_HOST` in LiteLLM set to `http://langfuse-web.langfuse.svc.cluster.local:3000`?

### 3.4 ClickHouse migration hangs

Cause: Keeper init is not finished, or `ON CLUSTER` is enabled on a single-node setup.

```bash
kubectl -n langfuse logs langfuse-clickhouse-0 -c clickhouse
kubectl -n langfuse exec langfuse-clickhouse-0 -- clickhouse-client \
  --query="SELECT * FROM system.zookeeper WHERE path='/'"
```

Fix: make sure `CLICKHOUSE_CLUSTER_ENABLED=false` is set in `langfuse-values.yaml`, then restart the worker.

### 3.5 ArgoCD app stays `OutOfSync` and does not self-heal

```bash
argocd app get <name>           # inspect sync result
argocd app sync <name> --prune  # force sync only after reviewing diff
kubectl -n argocd logs deploy/argocd-application-controller --tail=200
```

If it is stuck on a server-side apply conflict, remove the conflicting annotation or run `argocd app sync --replace`.

### 3.6 Loki query times out / logs are missing

```bash
kubectl -n loki logs sts/loki-loki --tail=200
kubectl -n loki get pods
```

- Is Promtail running as a DaemonSet on every node?
  `kubectl -n loki get pods -l app.kubernetes.io/name=promtail -o wide`
- Does the path glob match EKS containerd logs (`/var/log/pods/*/*/*.log`)?
- Is the compactor enabled for 14-day retention (see `loki.yaml`)?

### 3.7 LiteLLM P1000 "Authentication failed" after a fresh bootstrap

Symptoms: LiteLLM pods CrashLoop and logs show `prisma db error ... Authentication failed against database server`. Cause: Bitnami PostgreSQL regenerated a new password when a fresh PVC was created, but the legacy pre-baked database URL no longer matched the chart-generated password.

Permanent fix already applied: `litellm-values.yaml` builds `DATABASE_URL` from `POSTGRESQL_PASSWORD`. If this still happens:
1. Verify the password matches: `kubectl -n postgresql exec postgresql-primary-0 -- bash -c 'PGPASSWORD=$POSTGRESQL_PASSWORD psql -U postgres -c "SELECT 1"'`
2. Rotate `POSTGRESQL_PASSWORD` in AWS Secrets Manager, force-sync ESO in all 3 namespaces (`postgresql`, `litellm`, `langfuse`), then roll out both `postgresql-primary` and `litellm`.

### 3.8 Langfuse migration P3009 deadlock

Symptoms: `langfuse-web` pods CrashLoop and Prisma logs show `Error: P3009` plus `deadlock detected ... ExclusiveLock on advisory lock`. Cause: 2 `langfuse-web` replicas run `CREATE INDEX CONCURRENTLY` at the same time (migration `20240104210051_add_model_indices`).

Recovery (verified on 2026-06-05):
```bash
# 1. Scale both deployments down to 0
kubectl -n langfuse scale deploy/langfuse-web --replicas=0
kubectl -n langfuse scale deploy/langfuse-worker --replicas=0

# 2. Wait for pod termination, then delete the failed migration row
kubectl -n postgresql exec postgresql-primary-0 -- \
  bash -c 'PGPASSWORD=$POSTGRESQL_PASSWORD psql -U postgres langfuse \
  -c "DELETE FROM _prisma_migrations WHERE finished_at IS NULL"'

# 3. Patch HPA minReplicas to 1 (otherwise it may scale back to 2 and deadlock again)
kubectl -n langfuse patch hpa langfuse-web --type merge \
  -p '{"spec":{"minReplicas":1}}'

# 4. Scale to 1 replica and wait until Ready
kubectl -n langfuse scale deploy/langfuse-web --replicas=1
kubectl -n langfuse rollout status deploy/langfuse-web

# 5. Restore HPA minReplicas=2
kubectl -n langfuse patch hpa langfuse-web --type merge \
  -p '{"spec":{"minReplicas":2}}'
kubectl -n langfuse scale deploy/langfuse-worker --replicas=2
```

### 3.9 OIDC login does not show the Google button

Symptoms: refreshing the Open WebUI ALB still shows only the email/password form. `kubectl exec ... curl localhost:8080/api/config` returns an empty `"oauth":{"providers":{}}`.

Checklist:
1. Is `OAUTH_CLIENT_ID` present in the pod? `kubectl -n open-webui exec open-webui-0 -- env | grep OAUTH`. If missing, the key name in Secrets Manager is wrong.
2. Are the keys present in the Kubernetes secret? `kubectl -n open-webui get secret llmops-apikeys-secret -o jsonpath='{.data}' | python3 -c "import sys,json; print(sorted(json.load(sys.stdin).keys()))"` - it must include `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET`.
3. Common trap: pasting the credential into the AWS Console "Key/value" tab can accidentally turn the credential into the key name. Fix it by using `put-secret-value` with the correct JSON structure (RUNBOOK section 5.4).

### 3.10 kubectl shows "no such host" after recreating the cluster

Symptoms: `kubectl get nodes` returns `dial tcp: lookup <hash>.eks.amazonaws.com on 127.0.0.53:53: no such host`. The EKS cluster was recreated, the endpoint hash changed, and the old kubeconfig still points to the stale endpoint.

Fix: `aws eks update-kubeconfig --name llmops-cluster --region ap-southeast-1`.

### 3.11 Redis / Langfuse / Postgres down (degraded mode tests)

Required by Requirements section 8.1:

| Failure | Expected behavior | Test method |
|---|---|---|
| Redis down | LiteLLM chat still works, cache is lost | `kubectl -n redis scale sts/redis-master --replicas=0`; send a chat request -> it should still respond, with higher latency |
| Langfuse down | Chat still works, traces are lost | `kubectl -n langfuse scale deploy/langfuse-web --replicas=0`; chat should continue working |
| One LiteLLM pod crashes | API remains available | `kubectl -n litellm delete pod <pod>`; the remaining 2 replicas should continue serving |
| One Open WebUI pod crashes | UI remains available | `kubectl -n open-webui delete pod open-webui-0`; the other replica should continue serving |
| Postgres connections > 80% | `PostgresConnectionHigh` alert fires | Run a load test, inspect the Grafana panel; alert should fire within 5 minutes |

---

## 4. Change and deployment flow

### 4.1 Normal change deployment

1. Branch from `main`, change Helm values or manifests, then push your branch.
2. Open a PR and merge it into `main`.
3. After `main` updates, ArgoCD auto-syncs (`selfHeal + prune`).
4. Verify in Grafana and with `argocd app get <name>`.

### 4.2 Fast rollback

```bash
git revert <bad-commit> && git push
# or
argocd app rollback <name> <revision>
```

---

## 5. Secrets and key rotation

### 5.1 AWS Secrets Manager structure

`llmops/apikeys` (JSON) - required:
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

Optional (SSO/OIDC - if absent, Open WebUI still runs with local auth):
```
OIDC_CLIENT_ID             # Google OAuth client ID
OIDC_CLIENT_SECRET         # Google OAuth client secret
```

> `OPENID_PROVIDER_URL` is hard-coded in `open-webui-values.yaml` (Google discovery), so it does not need to live in Secrets Manager.
> LiteLLM now builds `DATABASE_URL` inline from `POSTGRESQL_PASSWORD` to avoid password drift between PostgreSQL and runtime configuration.

**Important when updating Secrets Manager in the Console UI**: use the `Plaintext` tab, not `Key/value`. If you add keys through `Key/value`, AWS Console can accidentally paste the credential into the key name, and ESO will sync a secret with empty env vars. This happened during the OIDC drill on 2026-06-05.

### 5.2 Key rotation - standard drill

Verified with a `WEBUI_SECRET_KEY` rotation drill on 2026-06-05: Secrets Manager update -> ESO refresh in under 30s -> StatefulSet rollout reaches 2/2 Ready in about 1.5 minutes.

1. Create a new key at the provider (for example, the OpenAI dashboard) or generate one locally:
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. Update Secrets Manager using the standard flow in section 5.4 (replace the value of the key being rotated).
3. Wait for `kubectl rollout status` on each affected workload before revoking the old provider-side key.

### 5.3 Enable SSO/OIDC - Google Workspace

`open-webui-values.yaml` already hard-codes Google's `OPENID_PROVIDER_URL`. You only need to add 2 keys to AWS Secrets Manager.

1. In **Google Cloud Console** -> APIs & Services -> Credentials -> **Create OAuth client ID**:
   - Application type: **Web application**
   - Name: `LLMOps Internal Chat`
   - Authorized redirect URIs: `http://internal-llmops-open-webui-54615089.ap-southeast-1.elb.amazonaws.com/oauth/oidc/callback`
     (change this to `https://chat.<domain>/oauth/oidc/callback` once you have a custom domain and ACM)
2. Copy the `Client ID` and `Client secret`.
3. Add both keys to AWS Secrets Manager using the section 5.4 flow. Example edit step:
   ```bash
   python3 -c "
   import json
   d = json.load(open('/tmp/sm.json'))
   d['OIDC_CLIENT_ID'] = '<paste client_id>'
   d['OIDC_CLIENT_SECRET'] = '<paste client_secret>'
   json.dump(d, open('/tmp/sm.json','w'))
   "
   ```
4. Verify by opening the ALB DNS name (through VPN) or using port-forward + localhost. The login form should show **Sign in with Google**.

Optional domain restriction: set env `OAUTH_ALLOWED_DOMAINS=company.com` in the Helm values.

### 5.4 Update AWS Secrets Manager - standard flow

This process applies to every change in `llmops/apikeys`: adding a new key, editing a value, rotating a key, or recovering a missing key. Follow it to avoid the 5 traps in section 5.5.

**Rule**: `put-secret-value` replaces the entire payload, it does not merge. Always read first, edit the full JSON payload, then push it back.

#### Step 1 - Read the current payload and verify

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

#### Step 2 - Edit the JSON

```bash
# Option A - Python one-liner for one change
python3 -c "
import json
d = json.load(open('/tmp/sm.json'))
d['NEW_KEY'] = 'new_value'        # add / update
# del d['OLD_KEY']                # delete
json.dump(d, open('/tmp/sm.json','w'))
"

# Option B - editor for multiple changes
vim /tmp/sm.json
```

#### Step 3 - Mandatory verification before push

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
assert not missing, 'STOP - required keys missing, do not push'
print('OK, safe to push. Keys:', sorted(d.keys()))
"
```

If `Missing required` appears -> STOP, fix the file, do not push.

#### Step 4 - Push + force-sync ESO + rollout

```bash
# 1. Push to Secrets Manager
aws secretsmanager put-secret-value --region ap-southeast-1 \
  --secret-id llmops/apikeys --secret-string file:///tmp/sm.json \
  --query VersionId --output text

# 2. Remove the temp file immediately
rm /tmp/sm.json

# 3. Force-sync ESO in EVERY namespace using this secret
for ns in $(kubectl get externalsecret -A \
  -o jsonpath='{range .items[?(@.metadata.name=="llmops-apikeys-secret")]}{.metadata.namespace}{"\n"}{end}'); do
  kubectl annotate externalsecret -n $ns llmops-apikeys-secret \
    force-sync=$(date +%s) --overwrite
done

# 4. Wait for the Kubernetes secret to update (if you added a new key, check that key)
until kubectl -n open-webui get secret llmops-apikeys-secret \
  -o jsonpath='{.data.NEW_KEY}' | grep -q .; do
  sleep 3
done

# 5. Roll out consumers
kubectl -n open-webui rollout restart statefulset/open-webui
kubectl -n litellm   rollout restart deploy/litellm
kubectl -n langfuse  rollout restart deploy/langfuse-web
kubectl -n langfuse  rollout restart deploy/langfuse-worker

# 6. Verify the env var in the pod
kubectl -n open-webui exec open-webui-0 -- env | grep NEW_KEY
```

#### Recovery - when a key was accidentally overwritten

Used successfully to recover `OIDC_CLIENT_ID` / `OIDC_CLIENT_SECRET` on 2026-06-05.

```bash
# 1. List versions
aws secretsmanager list-secret-version-ids --region ap-southeast-1 \
  --secret-id llmops/apikeys --include-deprecated \
  --query 'Versions[*].{VersionId:VersionId,Stages:VersionStages,Created:CreatedDate}' \
  --output table

# 2. Get the AWSPREVIOUS version (contains the missing key)
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

# 3. Merge missing keys from prev -> curr
python3 -c "
import json
prev = json.load(open('/tmp/prev.json'))
curr = json.load(open('/tmp/curr.json'))
recover = ['OIDC_CLIENT_ID','OIDC_CLIENT_SECRET']   # adjust this list as needed
for k in recover:
    if k in prev and k not in curr:
        curr[k] = prev[k]
        print(f'Restored {k}')
json.dump(curr, open('/tmp/curr.json','w'))
"

# 4. Push merged payload + force-sync + rollout (repeat Step 4 above)
aws secretsmanager put-secret-value --region ap-southeast-1 \
  --secret-id llmops/apikeys --secret-string file:///tmp/curr.json

rm /tmp/prev.json /tmp/curr.json
```

### 5.5 Common traps when updating Secrets Manager

| Trap | Symptom | Prevention |
|---|---|---|
| Credential pasted as the KEY name in the Console UI `Key/value` tab | ESO sync succeeds, but env vars are missing in the pod (`grep OAUTH_CLIENT_ID` returns nothing) | Always use CLI `put-secret-value` with JSON; if you must use the Console, choose **Plaintext**, not **Key/value** |
| `put-secret-value` replaces the whole payload | Old keys disappear after an update (for example, adding 2 OIDC keys and accidentally deleting `WEBUI_SECRET_KEY`) | Always `get-secret-value` first, edit the full JSON, then push |
| Forgot to force-sync ESO | Pod restart still uses old env because ESO has not pulled yet (default refresh interval is 1h) | Loop over namespaces and annotate `force-sync=$(date +%s)` on every matching ExternalSecret |
| Rolled out before the Kubernetes secret was updated | New pods still read the old value | Wait until `kubectl get secret ... | grep <new key>` shows the update before rollout |
| `/tmp/sm.json` left on disk too long | Credentials remain exposed on disk or in shell history | `rm` it immediately after push; clear history if any command included raw secret values |

---

## 6. Cost operations

- Platform-wide budget: `$6000 / 30d` (LiteLLM `general_settings.max_budget`).
- Per-team budget and model allowlist: PostSync job `argocd/rbac-setup` (calls the LiteLLM admin API).
- Spend monitoring: `Cost Analysis` dashboard plus alerts `DailyLLMCostSpike` (24h spend > $200) and `UserHighTokenConsumption` (one user > 30% of daily tokens).
- When a budget alert is firing: temporarily raise the limit via the admin API, then open a usage review ticket.

---

## 7. Backup and disaster recovery

| Asset | Mechanism | RPO |
|---|---|---|
| PostgreSQL | Daily gp3 EBS snapshot (terraform-managed) | 24h |
| Langfuse S3 traces | S3 versioning enabled on `llmops-langfuse-492372116094` | minutes |
| ClickHouse | Single-node EBS volume snapshot | 24h |
| ArgoCD config | Git is the single source of truth | 0 |

Restore PostgreSQL:
```bash
# Snapshot -> volume -> patch PVC -> roll PG primary
aws ec2 create-volume --snapshot-id <id> --availability-zone <az> --volume-type gp3
```

---

## 8. On-call contacts

- Platform owner: nghiatd (`nyclone002@gmail.com`)
- Escalation path: ArgoCD UI + Alertmanager logs (Slack webhook deferred - not configured yet).

---

## 9. RBAC and teams

5 teams are provisioned by the `argocd/rbac-setup` job (PostSync hook calling the LiteLLM admin API):

| Team | Allowlist | Budget / 30d | TPM | RPM |
|---|---|---|---|---|
| engineering | coding-assistant, fast-chat, long-context, private-chat | $100 | 500,000 | 500 |
| support | fast-chat | $40 | 150,000 | 150 |
| product | fast-chat | $30 | 100,000 | 100 |
| operations | fast-chat | $20 | 80,000 | 80 |
| executives | fast-chat, long-context | $10 | 50,000 | 50 |

Verify:
```bash
kubectl -n litellm exec deploy/litellm -- curl -s \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://localhost:4000/team/list | python3 -m json.tool
```

Create a virtual key for a new user (assign to a team):
```bash
curl -X POST http://litellm.litellm.svc/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -d '{"team_id":"engineering","user_id":"alice","max_budget":10}'
```

---

## 10. SLO verification

| SLO | Target | How to check |
|---|---|---|
| Open WebUI availability | 99.5% | Grafana panel `up{namespace="open-webui"}` summed over 30d |
| LiteLLM availability | 99.9% | Panel `up{namespace="litellm"}` summed over 30d |
| LiteLLM P95 latency | < 3s | `histogram_quantile(0.95, rate(litellm_request_duration_seconds_bucket[5m]))` |
| LLM request success rate | > 98% | `1 - rate(litellm_errors_total[5m]) / rate(litellm_requests_total[5m])` |
| Trace ingestion delay | < 60s | Langfuse worker queue depth dashboard |
| Alert detection time | < 5min | Alertmanager `firing_time - alert_start_time` |

If any SLO is missed for more than 24h -> open an incident, write a postmortem, and record it in `docs/weekly/`.

---

## 11. Alert Reference

All Prometheus rules are defined in `argocd/monitoring/prometheus-rules.yaml`. Quick lookup for on-call.

### llmops-platform (eval interval: 30s)

| Alert | Condition | Severity |
|---|---|---|
| LiteLLMHighErrorRate | failure rate > 5% for 5m | critical |
| LLMProviderHighTimeoutRate | timeout rate > 10% for 5m | warning |
| OpenWebUIDown | up == 0 for 2m | critical |
| LangfuseDown | up == 0 for 5m | warning |
| LiteLLMHighLatency | P95 > 3s for 10m | warning |
| RedisHighMemoryUsage | > 80% memory for 5m | warning |
| RedisHighConnectionCount | > 1000 clients for 5m | warning |
| PodRestartingFrequently | restart rate > 0.1/s over 15m window, for 5m (namespaces: litellm, langfuse, open-webui, redis) | warning |
| PodHighCPUUsage | > 90% CPU limit for 10m | warning |
| PodHighMemoryUsage | > 90% memory limit for 10m | warning |
| NodeUnderPressure | MemoryPressure or DiskPressure for 5m | warning |
| PostgreSQLHighConnectionUsage | > 80% of max_connections for 5m | warning |
| LangfuseIngestionDelayHigh | Redis queue >500 items for 10m | warning |

### llmops-cost-tracking (eval interval: 60s)

| Alert | Condition | Severity |
|---|---|---|
| DailyLLMCostSpike | 24h spend > $200 | warning |
| UserHighTokenConsumption | one user > 30% of daily tokens | warning |
| TeamHighTokenConsumption | one team > 50% of daily tokens | warning |

### llmops-security (eval interval: 30s)

| Alert | Condition | Severity |
|---|---|---|
| UnusualRequestPattern | request rate > 2× hourly average for 10m | warning |

### llmops-security-grafana (Grafana-managed, Loki datasource, eval interval: 5m)

| Alert | Condition | Severity | On-call action |
|---|---|---|---|
| SensitivePromptDetected | sensitive keyword in LiteLLM logs for 2m | warning | Open Log Explorer, filter namespace=litellm, check if real data or test traffic |

