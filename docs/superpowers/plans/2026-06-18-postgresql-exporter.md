# PostgreSQL Exporter Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a postgres_exporter sidecar to the PostgreSQL StatefulSet so Prometheus can scrape `pg_stat_activity_count` and `pg_settings_max_connections`, fixing the dead ServiceMonitor and the "no data" dashboard panel.

**Architecture:** A PostSync ArgoCD Job creates a `pg_monitor`-granted `exporter` user in the live PostgreSQL instance. A sidecar container runs postgres_exporter on port 9187 in the same pod, connecting via localhost. The existing ServiceMonitor is fixed to target the correct label and port, and Prometheus scrapes it cross-namespace into the `monitoring` namespace.

**Tech Stack:** Kubernetes, ArgoCD GitOps (auto-sync), bitnami/postgresql:latest (raw manifest StatefulSet), quay.io/prometheuscommunity/postgres-exporter:v0.19.1, kube-prometheus-stack ServiceMonitor CRD.

**Pre-requisite (manual, one-time — do this BEFORE pushing any code):**

Add `POSTGRES_EXPORTER_PASSWORD` to the `llmops/apikeys` secret in AWS Secrets Manager. The ExternalSecret uses `dataFrom.extract`, so any new key auto-surfaces in `llmops-apikeys-secret` — no YAML change needed.

```bash
aws secretsmanager get-secret-value \
  --secret-id llmops/apikeys \
  --query SecretString --output text > /tmp/sm.json

# Edit /tmp/sm.json — add: "POSTGRES_EXPORTER_PASSWORD": "<strong-password>"

aws secretsmanager put-secret-value \
  --secret-id llmops/apikeys \
  --secret-string file:///tmp/sm.json

rm /tmp/sm.json

# Force the ExternalSecret to re-sync immediately
kubectl annotate externalsecret llmops-apikeys-secret -n postgresql \
  force-sync=$(date +%s) --overwrite

# Verify the key landed in the Kubernetes Secret
kubectl get secret llmops-apikeys-secret -n postgresql \
  -o jsonpath='{.data.POSTGRES_EXPORTER_PASSWORD}' | base64 -d
# Expected: the password you set (non-empty)
```

---

## File Map

| File | Action | What changes |
|---|---|---|
| `argocd/apps/postgresql.yaml` | Modify line 18 | `include` glob: `postgresql.yaml` → `postgresql*.yaml` |
| `argocd/manifests/postgresql-exporter-user-job.yaml` | Create | PostSync Job that creates `exporter` user with `pg_monitor` |
| `argocd/manifests/postgresql.yaml` | Modify | Add sidecar container; add `metrics` port to `postgresql-primary` Service |
| `argocd/monitoring/service-monitors.yaml` | Modify lines 54-58 | Fix selector label and add `interval: 30s` |
| `argocd/helm-values/postgresql-values.yaml` | Modify lines 32-45 | Revert `metrics:` block to remove the no-op ServiceMonitor config |

---

### Task 1: Fix ArgoCD include filter

**Files:**
- Modify: `argocd/apps/postgresql.yaml:18`

This must land in git before the Job file — ArgoCD won't pick up `postgresql-exporter-user-job.yaml` until the glob is widened.

- [ ] **Step 1: Edit the include filter**

In `argocd/apps/postgresql.yaml`, change line 18:

```yaml
        include: "postgresql*.yaml"
```

(was `include: "postgresql.yaml"`)

- [ ] **Step 2: Verify the diff**

```bash
git diff argocd/apps/postgresql.yaml
```

Expected output shows exactly one line changed: `postgresql.yaml` → `postgresql*.yaml`. Nothing else.

- [ ] **Step 3: Commit**

```bash
git add argocd/apps/postgresql.yaml
git commit -m "fix: widen argocd postgresql app include to pick up new job file"
```

---

### Task 2: Create the PostSync Job

**Files:**
- Create: `argocd/manifests/postgresql-exporter-user-job.yaml`

This Job runs once after every ArgoCD sync, creates the `exporter` PostgreSQL user with the `pg_monitor` role, and cleans itself up. The `IF NOT EXISTS` guard makes it safe to re-run.

- [ ] **Step 1: Create the file**

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: create-postgres-exporter-user
  namespace: postgresql
  annotations:
    argocd.argoproj.io/hook: PostSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded,BeforeHookCreation
spec:
  activeDeadlineSeconds: 120
  backoffLimit: 5
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: create-user
          image: bitnami/postgresql:latest
          command: [bash, -c]
          args:
            - |
              until pg_isready -h postgresql-primary -U postgres; do
                echo "Waiting for PostgreSQL..."
                sleep 3
              done
              PGPASSWORD=$POSTGRESQL_PASSWORD psql \
                -h postgresql-primary \
                -U postgres \
                -d postgres \
                -c "DO \$\$ BEGIN
                  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'exporter') THEN
                    CREATE USER exporter WITH PASSWORD '$POSTGRES_EXPORTER_PASSWORD' CONNECTION LIMIT 3;
                    GRANT pg_monitor TO exporter;
                  END IF;
                END \$\$;"
          env:
            - name: POSTGRESQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: llmops-apikeys-secret
                  key: POSTGRESQL_PASSWORD
            - name: POSTGRES_EXPORTER_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: llmops-apikeys-secret
                  key: POSTGRES_EXPORTER_PASSWORD
          resources:
            requests: {cpu: 50m, memory: 64Mi}
            limits: {cpu: 100m, memory: 128Mi}
```

- [ ] **Step 2: Verify file exists and is valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('argocd/manifests/postgresql-exporter-user-job.yaml'))" && echo "OK"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add argocd/manifests/postgresql-exporter-user-job.yaml
git commit -m "feat: add argocd postsync job to create postgres exporter user"
```

---

### Task 3: Add sidecar and metrics port to postgresql.yaml

**Files:**
- Modify: `argocd/manifests/postgresql.yaml`

Two changes in one file — keep them in a single commit:
1. Add `postgres-exporter` as a second container in the StatefulSet pod spec (after the `postgresql` container)
2. Add a `metrics` port to the `postgresql-primary` Service

- [ ] **Step 1: Add the sidecar container**

In `argocd/manifests/postgresql.yaml`, after the closing of the `postgresql` container block (after the `livenessProbe` block, before `volumes:`), add:

```yaml
        - name: postgres-exporter
          image: quay.io/prometheuscommunity/postgres-exporter:v0.19.1
          securityContext:
            runAsNonRoot: true
            runAsUser: 65534
            runAsGroup: 65534
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          env:
            - name: DATA_SOURCE_URI
              value: "localhost:5432/postgres?sslmode=disable"
            - name: DATA_SOURCE_USER
              value: "exporter"
            - name: DATA_SOURCE_PASS
              valueFrom:
                secretKeyRef:
                  name: llmops-apikeys-secret
                  key: POSTGRES_EXPORTER_PASSWORD
          ports:
            - name: metrics
              containerPort: 9187
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          readinessProbe:
            httpGet:
              path: /metrics
              port: 9187
            initialDelaySeconds: 10
            periodSeconds: 15
          livenessProbe:
            httpGet:
              path: /metrics
              port: 9187
            initialDelaySeconds: 30
            periodSeconds: 30
```

The full `containers:` block after the edit should have two entries: `postgresql` (existing) and `postgres-exporter` (new).

- [ ] **Step 2: Add metrics port to the postgresql-primary Service**

In the same file, find the `postgresql-primary` Service (the ClusterIP one, not the headless one). Its `ports:` block currently has only the `postgresql` port. Add the `metrics` port:

```yaml
  ports:
    - name: postgresql
      port: 5432
      targetPort: 5432
    - name: metrics
      port: 9187
      targetPort: 9187
```

Do NOT modify the `postgresql-primary-headless` Service.

- [ ] **Step 3: Verify the YAML is valid**

```bash
python3 -c "
import yaml
docs = list(yaml.safe_load_all(open('argocd/manifests/postgresql.yaml')))
print(f'Documents: {len(docs)}')
sts = next(d for d in docs if d['kind'] == 'StatefulSet')
containers = sts['spec']['template']['spec']['containers']
print(f'Containers: {[c[\"name\"] for c in containers]}')
svc = next(d for d in docs if d['kind'] == 'Service' and d['metadata']['name'] == 'postgresql-primary')
ports = [p['name'] for p in svc['spec']['ports']]
print(f'Service ports: {ports}')
"
```

Expected output:
```
Documents: 5
Containers: ['postgresql', 'postgres-exporter']
Service ports: ['postgresql', 'metrics']
```

- [ ] **Step 4: Commit**

```bash
git add argocd/manifests/postgresql.yaml
git commit -m "feat: add postgres-exporter sidecar and metrics port to postgresql statefulset"
```

---

### Task 4: Fix the ServiceMonitor and revert postgresql-values.yaml

**Files:**
- Modify: `argocd/monitoring/service-monitors.yaml:54-58`
- Modify: `argocd/helm-values/postgresql-values.yaml:32-45`

Two housekeeping changes — one fixes the live ServiceMonitor, the other removes a misleading no-op.

- [ ] **Step 1: Fix the postgresql ServiceMonitor selector**

In `argocd/monitoring/service-monitors.yaml`, find the `postgresql` ServiceMonitor (starts at line 44). Replace the `selector` and `endpoints` block:

```yaml
  selector:
    matchLabels:
      app: postgresql-primary
  endpoints:
    - port: metrics
      interval: 30s
      path: /metrics
```

(was: `app.kubernetes.io/name: postgresql`, no `interval`)

The `metadata`, `namespace`, `labels`, and `namespaceSelector` blocks are unchanged.

- [ ] **Step 2: Revert postgresql-values.yaml**

The `metrics:` block in `argocd/helm-values/postgresql-values.yaml` was mistakenly enabled in a previous commit. ArgoCD does not use this file for the postgresql deployment (it sources raw manifests, not Helm). Revert the `metrics:` block to remove it entirely:

```yaml
image:
  tag: latest

auth:
  existingSecret: llmops-apikeys-secret
  secretKeys:
    adminPasswordKey: POSTGRESQL_PASSWORD
  database: "postgres"

primary:
  persistence:
    enabled: true
    size: 20Gi
    storageClass: "gp3"

  resources:
    requests:
      memory: "256Mi"
      cpu: "250m"
    limits:
      memory: "1Gi"
      cpu: "1000m"

  initdb:
    scripts:
      init.sql: |
        CREATE SCHEMA IF NOT EXISTS litellm;
        CREATE SCHEMA IF NOT EXISTS langfuse;
        GRANT ALL PRIVILEGES ON SCHEMA litellm TO postgres;
        GRANT ALL PRIVILEGES ON SCHEMA langfuse TO postgres;
```

(Remove the entire `metrics:` block that starts at the current line 32.)

- [ ] **Step 3: Verify service-monitors.yaml**

```bash
python3 -c "
import yaml
docs = list(yaml.safe_load_all(open('argocd/monitoring/service-monitors.yaml')))
pg = next(d for d in docs if d['metadata']['name'] == 'postgresql')
print('selector:', pg['spec']['selector']['matchLabels'])
print('endpoint port:', pg['spec']['endpoints'][0]['port'])
print('endpoint interval:', pg['spec']['endpoints'][0].get('interval'))
"
```

Expected:
```
selector: {'app': 'postgresql-primary'}
endpoint port: metrics
endpoint interval: 30s
```

- [ ] **Step 4: Verify postgresql-values.yaml has no metrics block**

```bash
grep -n "metrics:" argocd/helm-values/postgresql-values.yaml
```

Expected: no output (empty).

- [ ] **Step 5: Commit**

```bash
git add argocd/monitoring/service-monitors.yaml argocd/helm-values/postgresql-values.yaml
git commit -m "fix: correct postgresql servicemonitor selector and revert no-op values"
```

---

## Verification (after ArgoCD syncs)

ArgoCD auto-sync is enabled. After pushing, watch the sync complete (≤2 min normally):

```bash
# Watch ArgoCD sync status
kubectl get application postgresql -n argocd -w
```

**Step 1: Confirm pod has two containers**

```bash
kubectl get pods -n postgresql
# Expected: postgresql-primary-0   2/2   Running
```

**Step 2: Confirm exporter metrics endpoint responds**

```bash
kubectl exec -n postgresql postgresql-primary-0 -c postgres-exporter -- \
  wget -qO- http://localhost:9187/metrics | grep pg_stat_activity_count | head -3
```

Expected: lines like `pg_stat_activity_count{...} 3`

**Step 3: Confirm PostSync Job ran and succeeded**

```bash
kubectl get jobs -n postgresql
# Expected: create-postgres-exporter-user   1/1   Complete   (then deleted by HookSucceeded)
# If already deleted, check events:
kubectl get events -n postgresql --sort-by='.lastTimestamp' | tail -10
```

**Step 4: Confirm Prometheus is scraping**

```bash
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090 &
# Then open http://localhost:9090 and query: pg_stat_activity_count
# Expected: returns time series with values
```

**Step 5: Confirm Grafana panel shows data**

Open the LLMOps Platform Overview dashboard. Expand the Infrastructure row. The `PostgreSQL Conn Usage` gauge should show a non-zero percentage (active connections / max_connections).
