# LiteLLM Secrets Management Design

**Date:** 2026-05-29  
**Status:** Approved  
**Author:** Claude Opus 4.6

## Problem

LiteLLM deployment requires secrets (API keys, database credentials) but ArgoCD's auto-prune deletes manually created secrets. The cluster already has External Secrets Operator configured with AWS Secrets Manager, but litellm namespace doesn't have ExternalSecret resources defined.

## Solution

Add ExternalSecret resources to the litellm Helm chart that pull secrets from AWS Secrets Manager into the litellm namespace, following the existing pattern used in the default namespace.

## Architecture

### Components

1. **ExternalSecret: llmops-apikeys-secret**
   - Source: AWS Secrets Manager path `llmops/apikeys`
   - Contains: API keys for OpenAI, Anthropic, Gemini, Langfuse, LiteLLM master key, Redis password
   - Refresh: Every 1 hour
   - References: ClusterSecretStore `aws-secrets-manager`

2. **ExternalSecret: llmops-supabase-secret**
   - Source: AWS Secrets Manager path `llmops/supabase`
   - Contains: Database connection string for LiteLLM spend tracking
   - Refresh: Every 1 hour
   - References: ClusterSecretStore `aws-secrets-manager`

### Data Flow

```
AWS Secrets Manager
    ↓ (External Secrets Operator fetches)
ClusterSecretStore (aws-secrets-manager)
    ↓ (ExternalSecret references)
Kubernetes Secrets (litellm namespace)
    ↓ (mounted as env vars)
LiteLLM Pods
```

### File Structure

```
argocd/helm-values/litellm-chart/
├── templates/
│   ├── deployment.yaml          (existing - references secrets)
│   ├── service.yaml             (existing)
│   ├── configmap.yaml           (existing)
│   ├── externalsecret.yaml      (NEW - creates ExternalSecret resources)
│   └── ...
└── values.yaml                  (existing)
```

## Implementation Details

### ExternalSecret Template

Create `argocd/helm-values/litellm-chart/templates/externalsecret.yaml` with two resources:

**Resource 1: llmops-apikeys-secret**
- Pulls from `llmops/apikeys` in AWS Secrets Manager
- Maps keys: anthropic_api_key → ANTHROPIC_API_KEY, etc.
- Uses template transformation to match existing secret format

**Resource 2: llmops-supabase-secret**
- Pulls from `llmops/supabase` in AWS Secrets Manager
- Maps keys: litellm_db_url → LITELLM_DB_URL

Both use:
- `refreshInterval: 1h`
- `secretStoreRef.kind: ClusterSecretStore`
- `secretStoreRef.name: aws-secrets-manager`
- `target.creationPolicy: Owner`
- `target.deletionPolicy: Retain`

### No Changes Required

- **Deployment:** Already references `llmops-apikeys-secret` and `llmops-supabase-secret`
- **Values:** No secret values in Git (managed by External Secrets)
- **ArgoCD:** ExternalSecret is a valid Kubernetes resource, won't be pruned

## Error Handling

1. **AWS Secrets Manager unavailable:** External Secrets Operator retries automatically
2. **Secret doesn't exist in AWS:** ExternalSecret shows error status, pods wait
3. **ClusterSecretStore misconfigured:** ExternalSecret shows error status
4. **Namespace deleted:** Secrets recreated when namespace recreated by ArgoCD

## Testing

1. Apply the ExternalSecret resources
2. Verify External Secrets Operator creates the Kubernetes secrets
3. Verify LiteLLM pods start successfully
4. Verify secrets refresh after 1 hour
5. Test ArgoCD sync doesn't prune the secrets

## Rollout Plan

1. Create ExternalSecret template
2. Commit to Git
3. Push to GitHub
4. ArgoCD auto-syncs and creates ExternalSecret resources
5. External Secrets Operator creates Kubernetes secrets
6. LiteLLM pods restart and mount secrets
7. Verify application health

## Success Criteria

- LiteLLM pods running with status `Ready`
- Secrets exist in litellm namespace
- ArgoCD application shows `Synced` and `Healthy`
- Secrets auto-refresh from AWS Secrets Manager
- No manual secret management required
