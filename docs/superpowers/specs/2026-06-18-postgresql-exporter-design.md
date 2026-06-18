# PostgreSQL Exporter Sidecar Design

**Date:** 2026-06-18
**Status:** Approved

## Context

PostgreSQL is deployed as a raw Kubernetes manifest (`argocd/manifests/postgresql.yaml`) — not a Helm chart. No postgres_exporter exists, so `pg_stat_activity_count` and `pg_settings_max_connections` metrics are absent from Prometheus. This causes:

- `PostgreSQLHighConnectionUsage` alert in `prometheus-rules.yaml` — never fires (dead rule)
- `PostgreSQL Connection Usage` gauge panel in the Overview dashboard — always "no data"
- `ServiceMonitor/postgresql` in `service-monitors.yaml` — targeting a `metrics` port that doesn't exist, and using a wrong label selector (`app.kubernetes.io/name: postgresql` vs actual label `app: postgresql-primary`)

## Goals

- Expose `pg_stat_activity_count` and `pg_settings_max_connections` to Prometheus
- Fix the ServiceMonitor label selector and port reference
- Keep everything in GitOps — no manual cluster steps beyond adding the secret key to AWS Secrets Manager

## Non-Goals

- Custom query collectors (default metrics are sufficient)
- TLS between exporter and PostgreSQL (loopback connection, sslmode=disable is safe)
- Modifying `postgresql-values.yaml` (that file is not used by ArgoCD for this deployment)

---

## Design

### Step 0: Add secret key to AWS Secrets Manager (manual, one-time)

Add `POSTGRES_EXPORTER_PASSWORD` to the `llmops/apikeys` JSON payload in AWS Secrets Manager. The ExternalSecret in `postgresql-secret.yaml` uses `dataFrom.extract` on `llmops/apikeys`, so any new key added there automatically surfaces in the `llmops-apikeys-secret` Kubernetes Secret — no YAML change needed.

**RUNBOOK reminder:** `put-secret-value` replaces the entire payload. Always read first, edit the full JSON, then push it back. Remove `/tmp/sm.json` after.

```bash
aws secretsmanager get-secret-value \
  --secret-id llmops/apikeys \
  --query SecretString --output text > /tmp/sm.json

# Edit /tmp/sm.json — add: "POSTGRES_EXPORTER_PASSWORD": "<strong-password>"

aws secretsmanager put-secret-value \
  --secret-id llmops/apikeys \
  --secret-string file:///tmp/sm.json

rm /tmp/sm.json
```

After pushing, force-sync the ExternalSecret in the `postgresql` namespace:
```bash
kubectl annotate externalsecret llmops-apikeys-secret -n postgresql \
  force-sync=$(date +%s) --overwrite
```

### Step 1: Fix ArgoCD app include filter

Modify `argocd/apps/postgresql.yaml` — change the `include` filter so ArgoCD picks up the new Job file:

```yaml
# was: include: "postgresql.yaml"
include: "postgresql*.yaml"
```

Without this change, `postgresql-exporter-user-job.yaml` is ignored by ArgoCD and the monitoring user is never created.

### Step 2: Create the monitoring user — one-shot Job

New file: `argocd/manifests/postgresql-exporter-user-job.yaml`

Since the initdb scripts only run on first pod creation and PostgreSQL already has data, a Job creates the monitoring role against the live instance.

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

**Delete policy:** `HookSucceeded,BeforeHookCreation` — `HookSucceeded` cleans up after success; `BeforeHookCreation` deletes any leftover Job from a previous failed run before creating a new one. Without `BeforeHookCreation`, a failed Job would block subsequent syncs with an "already exists" error.

**Readiness wait:** `pg_isready` loop guards against the window between ArgoCD declaring the StatefulSet healthy and PostgreSQL actually accepting TCP connections. Bounded by `activeDeadlineSeconds: 120`.

**Idempotency:** The `IF NOT EXISTS` guard makes repeated runs safe.

### Step 3: Add postgres_exporter sidecar to the StatefulSet

Modify `argocd/manifests/postgresql.yaml` — add a second container to the StatefulSet pod spec:

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

The sidecar connects via `localhost` (shared pod network). Bitnami runs as UID 1001; the exporter runs as UID 65534 — no conflict.

### Step 4: Add metrics port to the Service

Modify the `postgresql-primary` Service in `argocd/manifests/postgresql.yaml`:

```yaml
ports:
  - name: postgresql
    port: 5432
    targetPort: 5432
  - name: metrics       # add this
    port: 9187
    targetPort: 9187
```

### Step 5: Fix the ServiceMonitor

Modify `argocd/monitoring/service-monitors.yaml` — fix the `postgresql` ServiceMonitor:

```yaml
spec:
  namespaceSelector:
    matchNames:
      - postgresql
  selector:
    matchLabels:
      app: postgresql-primary   # was: app.kubernetes.io/name: postgresql
  endpoints:
    - port: metrics
      interval: 30s
      path: /metrics
```

### Step 6: Revert postgresql-values.yaml

Revert the no-op ServiceMonitor change made to `argocd/helm-values/postgresql-values.yaml` (the file is unused by ArgoCD for this deployment — leaving it with `enabled: true` is misleading).

---

## Verification

After ArgoCD syncs:

```bash
# Confirm sidecar is running
kubectl get pods -n postgresql

# Confirm metrics endpoint responds
kubectl exec -n postgresql postgresql-primary-0 -c postgres-exporter -- \
  wget -qO- http://localhost:9187/metrics | grep pg_stat_activity_count | head -5

# Confirm Prometheus is scraping
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
# Then query: pg_stat_activity_count
```

---

## Files Changed

| File | Change |
|---|---|
| `argocd/apps/postgresql.yaml` | Change `include` filter to `postgresql*.yaml` |
| `argocd/manifests/postgresql-exporter-user-job.yaml` | New — PostSync Job to create `exporter` user |
| `argocd/manifests/postgresql.yaml` | Add sidecar container + metrics port to Service |
| `argocd/monitoring/service-monitors.yaml` | Fix ServiceMonitor label selector |
| `argocd/helm-values/postgresql-values.yaml` | Revert no-op ServiceMonitor change |
