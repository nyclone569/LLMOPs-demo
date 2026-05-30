# LiteLLM External Secrets Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ExternalSecret resources to litellm Helm chart to pull secrets from AWS Secrets Manager

**Architecture:** Create a Kubernetes ExternalSecret template that references the existing ClusterSecretStore (aws-secrets-manager) and pulls secrets from the same AWS Secrets Manager paths used by the default namespace. External Secrets Operator will automatically create and sync Kubernetes secrets in the litellm namespace.

**Tech Stack:** Kubernetes, External Secrets Operator, Helm, ArgoCD

---

## File Structure

**Files to create:**
- `argocd/helm-values/litellm-chart/templates/externalsecret.yaml` - ExternalSecret resources for API keys and database credentials

**Files to modify:**
- None (deployment already references the secret names)

---

### Task 1: Create ExternalSecret for API Keys

**Files:**
- Create: `argocd/helm-values/litellm-chart/templates/externalsecret.yaml`

- [ ] **Step 1: Create the ExternalSecret template file**

Create `argocd/helm-values/litellm-chart/templates/externalsecret.yaml`:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: llmops-apikeys-secret
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "litellm.labels" . | nindent 4 }}
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: aws-secrets-manager
  target:
    name: llmops-apikeys-secret
    creationPolicy: Owner
    deletionPolicy: Retain
    template:
      engineVersion: v2
      mergePolicy: Replace
      data:
        ANTHROPIC_API_KEY: '{{ "{{ .anthropic_api_key }}" }}'
        GEMINI_API_KEY: '{{ "{{ .gemini_api_key }}" }}'
        LANGFUSE_PUBLIC_KEY: '{{ "{{ .langfuse_public_key }}" }}'
        LANGFUSE_SECRET_KEY: '{{ "{{ .langfuse_secret_key }}" }}'
        LITELLM_MASTER_KEY: '{{ "{{ .litellm_master_key }}" }}'
        LITELLM_SALT_KEY: '{{ "{{ .litellm_salt_key }}" }}'
        OPENAI_API_KEY: '{{ "{{ .openai_api_key }}" }}'
        REDIS_PASSWORD: '{{ "{{ .redis_password }}" }}'
        WEBUI_SECRET_KEY: '{{ "{{ .webui_secret_key }}" }}'
  dataFrom:
    - extract:
        key: llmops/apikeys
        conversionStrategy: Default
        decodingStrategy: None
        metadataPolicy: None
```

- [ ] **Step 2: Verify the file was created**

Run: `cat argocd/helm-values/litellm-chart/templates/externalsecret.yaml | head -20`

Expected: File exists and shows the ExternalSecret resource

- [ ] **Step 3: Commit the API keys ExternalSecret**

```bash
git add argocd/helm-values/litellm-chart/templates/externalsecret.yaml
git commit -m "feat(litellm): add ExternalSecret for API keys

Add ExternalSecret resource to pull API keys from AWS Secrets Manager
into litellm namespace. References existing ClusterSecretStore and
syncs secrets every hour.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add ExternalSecret for Supabase Database Credentials

**Files:**
- Modify: `argocd/helm-values/litellm-chart/templates/externalsecret.yaml`

- [ ] **Step 1: Add separator and second ExternalSecret resource**

Append to `argocd/helm-values/litellm-chart/templates/externalsecret.yaml`:

```yaml
---
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: llmops-supabase-secret
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "litellm.labels" . | nindent 4 }}
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: aws-secrets-manager
  target:
    name: llmops-supabase-secret
    creationPolicy: Owner
    deletionPolicy: Retain
    template:
      engineVersion: v2
      mergePolicy: Replace
      data:
        LITELLM_DB_URL: '{{ "{{ .litellm_db_url }}" }}'
  dataFrom:
    - extract:
        key: llmops/supabase
        conversionStrategy: Default
        decodingStrategy: None
        metadataPolicy: None
```

- [ ] **Step 2: Verify both ExternalSecrets are in the file**

Run: `grep -c "kind: ExternalSecret" argocd/helm-values/litellm-chart/templates/externalsecret.yaml`

Expected: Output is `2`

- [ ] **Step 3: Commit the Supabase ExternalSecret**

```bash
git add argocd/helm-values/litellm-chart/templates/externalsecret.yaml
git commit -m "feat(litellm): add ExternalSecret for Supabase credentials

Add ExternalSecret resource to pull database connection string from
AWS Secrets Manager for LiteLLM spend tracking.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Revert Unnecessary ArgoCD Configuration Change

**Files:**
- Modify: `argocd/apps/litellm.yaml`

- [ ] **Step 1: Check current git status**

Run: `git status argocd/apps/litellm.yaml`

Expected: Shows `modified: argocd/apps/litellm.yaml`

- [ ] **Step 2: Revert the ignoreDifferences changes**

Run: `git checkout argocd/apps/litellm.yaml`

Expected: File reverted to original state

- [ ] **Step 3: Verify the file is clean**

Run: `git status argocd/apps/litellm.yaml`

Expected: No output (file is clean)

---

### Task 4: Push Changes and Trigger ArgoCD Sync

**Files:**
- None (Git and Kubernetes operations)

- [ ] **Step 1: Push commits to GitHub**

Run: `git push origin main`

Expected: Commits pushed successfully

- [ ] **Step 2: Force ArgoCD to refresh litellm application**

Run: `kubectl annotate application litellm -n argocd argocd.argoproj.io/refresh=hard --overwrite`

Expected: `application.argoproj.io/litellm annotated`

- [ ] **Step 3: Wait for ArgoCD to sync**

Run: `sleep 15 && kubectl get application litellm -n argocd`

Expected: Shows `Synced` status (may still be `Progressing` health)

---

### Task 5: Verify External Secrets Operator Creates Secrets

**Files:**
- None (Verification only)

- [ ] **Step 1: Check ExternalSecret resources were created**

Run: `kubectl get externalsecrets -n litellm`

Expected: Shows two ExternalSecrets: `llmops-apikeys-secret` and `llmops-supabase-secret`

- [ ] **Step 2: Check ExternalSecret status**

Run: `kubectl get externalsecrets -n litellm -o wide`

Expected: Both show `STATUS: SecretSynced` and `READY: True`

- [ ] **Step 3: Verify Kubernetes secrets were created**

Run: `kubectl get secrets -n litellm | grep llmops`

Expected: Shows `llmops-apikeys-secret` and `llmops-supabase-secret`

- [ ] **Step 4: Verify secret has correct keys**

Run: `kubectl get secret llmops-apikeys-secret -n litellm -o jsonpath='{.data}' | jq 'keys'`

Expected: Shows array with keys: `["ANTHROPIC_API_KEY", "GEMINI_API_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LITELLM_MASTER_KEY", "LITELLM_SALT_KEY", "OPENAI_API_KEY", "REDIS_PASSWORD", "WEBUI_SECRET_KEY"]`

---

### Task 6: Verify LiteLLM Pods Start Successfully

**Files:**
- None (Verification only)

- [ ] **Step 1: Check pod status**

Run: `kubectl get pods -n litellm`

Expected: Pods show `Running` status (may take 30-60 seconds)

- [ ] **Step 2: Wait for pods to become ready**

Run: `kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=litellm -n litellm --timeout=120s`

Expected: Pods become ready within 2 minutes

- [ ] **Step 3: Check pod logs for successful startup**

Run: `kubectl logs -n litellm -l app.kubernetes.io/name=litellm --tail=20 | grep -i "ready\|started\|listening"`

Expected: Shows LiteLLM started successfully and listening on port 4000

- [ ] **Step 4: Verify no CreateContainerConfigError**

Run: `kubectl get pods -n litellm -o jsonpath='{.items[*].status.containerStatuses[*].state}' | grep -i error || echo "No errors"`

Expected: Output is `No errors`

---

### Task 7: Verify ArgoCD Application Health

**Files:**
- None (Verification only)

- [ ] **Step 1: Check ArgoCD application status**

Run: `kubectl get application litellm -n argocd`

Expected: Shows `SYNC STATUS: Synced` and `HEALTH STATUS: Healthy`

- [ ] **Step 2: Check all ArgoCD applications**

Run: `kubectl get applications -n argocd`

Expected: All applications show healthy status (redis, litellm, kube-prometheus-stack, etc.)

- [ ] **Step 3: Verify no sync errors**

Run: `kubectl get application litellm -n argocd -o jsonpath='{.status.conditions}' | jq '.[] | select(.type=="SyncError")'`

Expected: No output (no sync errors)

---

## Self-Review Checklist

**Spec coverage:**
- ✅ ExternalSecret for llmops-apikeys-secret (Task 1)
- ✅ ExternalSecret for llmops-supabase-secret (Task 2)
- ✅ Push to Git and trigger sync (Task 4)
- ✅ Verify External Secrets Operator creates secrets (Task 5)
- ✅ Verify LiteLLM pods start (Task 6)
- ✅ Verify ArgoCD health (Task 7)

**Placeholder scan:**
- ✅ No TBD, TODO, or placeholders
- ✅ All code blocks complete
- ✅ All commands have expected output

**Type consistency:**
- ✅ Secret names consistent: `llmops-apikeys-secret`, `llmops-supabase-secret`
- ✅ Namespace references consistent: `{{ .Release.Namespace }}`
- ✅ ClusterSecretStore name consistent: `aws-secrets-manager`

---

## Success Criteria

- [ ] ExternalSecret resources created in litellm namespace
- [ ] Kubernetes secrets created by External Secrets Operator
- [ ] LiteLLM pods running with `Ready` status
- [ ] ArgoCD application shows `Synced` and `Healthy`
- [ ] No CreateContainerConfigError in pods
- [ ] Secrets will auto-refresh every hour from AWS Secrets Manager
