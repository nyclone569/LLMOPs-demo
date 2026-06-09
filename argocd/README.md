# ArgoCD GitOps Configuration for LLMOps Platform

This directory contains the complete ArgoCD GitOps configuration for deploying and managing the LLMOps platform on EKS.

## Architecture Overview

The LLMOps platform consists of:

1. **Redis** - In-cluster caching for LiteLLM
2. **Langfuse** - LLM observability and analytics
3. **LiteLLM** - Unified LLM proxy gateway
4. **Open WebUI** - Chat interface for end users

## Directory Structure

```
argocd/
├── projects/
│   └── llmops-project.yaml         # ArgoCD AppProject
├── apps/
│   ├── root-app.yaml               # App of Apps (manages all below)
│   ├── redis.yaml                  # Redis application
│   ├── langfuse.yaml               # Langfuse application
│   ├── litellm.yaml                # LiteLLM application
│   └── open-webui.yaml             # Open WebUI application
└── helm-values/
    ├── redis-values.yaml           # Redis Helm values
    ├── langfuse-values.yaml        # Langfuse Helm values
    ├── litellm-values.yaml         # LiteLLM Helm values
    └── open-webui-values.yaml      # Open WebUI Helm values
```

## Prerequisites

Before deploying, ensure you have:

1. **EKS Cluster** running with:
   - ArgoCD installed in `argocd` namespace
   - External Secrets Operator installed
   - AWS Load Balancer Controller installed
   - Cert Manager installed (for TLS)

2. **AWS Secrets Manager** with secrets:
   - Secret containing: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_S3_ACCESS_KEY_ID`, `LANGFUSE_S3_SECRET_ACCESS_KEY`, `REDIS_PASSWORD`, `POSTGRESQL_PASSWORD`, `CLICKHOUSE_PASSWORD`, `WEBUI_SECRET_KEY`

3. **Kubernetes Secrets** synced by External Secrets:
   - `llmops-apikeys-secret` (in each namespace that needs shared credentials)

4. **In-cluster services**:
   - PostgreSQL for Langfuse and LiteLLM metadata
   - Redis for LiteLLM caching

## Configuration Steps

### 1. Update Git Repository URLs

Update the following files with your Git repository URL:

```bash
# In argocd/apps/root-app.yaml
repoURL: https://github.com/YOUR_ORG/YOUR_REPO.git

# In argocd/apps/litellm.yaml
repoURL: https://github.com/YOUR_ORG/YOUR_REPO.git
```

### 2. Update Domain Names

Replace `yourdomain.com` with your actual domain in:
- `argocd/helm-values/langfuse-values.yaml`
- `argocd/helm-values/litellm-values.yaml`
- `argocd/helm-values/open-webui-values.yaml`

### 3. Update Security Groups

Update ALB security group IDs in ingress annotations:
- `argocd/helm-values/langfuse-values.yaml`
- `argocd/helm-values/litellm-values.yaml`
- `argocd/helm-values/open-webui-values.yaml`

Look for: `alb.ingress.kubernetes.io/security-groups: "sg-xxxxx"`

### 4. Create External Secrets

Create ExternalSecret resources to sync from AWS Secrets Manager:

```yaml
# Example: argocd/external-secrets/apikeys-secret.yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: llmops-apikeys-external
  namespace: langfuse  # Repeat for each namespace
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: llmops-apikeys-secret
    creationPolicy: Owner
  data:
    - secretKey: OPENAI_API_KEY
      remoteRef:
        key: llmops/apikeys
        property: OPENAI_API_KEY
    - secretKey: LITELLM_MASTER_KEY
      remoteRef:
        key: llmops/apikeys
        property: LITELLM_MASTER_KEY
    # ... add all other keys
```

## Deployment

### Option 1: Deploy via ArgoCD UI

1. Apply the AppProject:
```bash
kubectl apply -f argocd/projects/llmops-project.yaml
```

2. Apply the root app:
```bash
kubectl apply -f argocd/apps/root-app.yaml
```

3. The root app will automatically deploy all child applications.

### Option 2: Deploy via kubectl

```bash
# Apply AppProject
kubectl apply -f argocd/projects/llmops-project.yaml

# Apply all applications
kubectl apply -f argocd/apps/
```

### Option 3: Deploy via ArgoCD CLI

```bash
# Login to ArgoCD
argocd login <argocd-server>

# Create AppProject
argocd proj create -f argocd/projects/llmops-project.yaml

# Create root app
argocd app create -f argocd/apps/root-app.yaml

# Sync the root app
argocd app sync llmops-root
```

## Deployment Order

Applications are deployed in the following order (via syncWave):

1. **Redis** (syncWave: 0) - Base dependency
2. **Langfuse** (syncWave: 10) - Observability platform
3. **LiteLLM** (syncWave: 20) - Gateway (depends on Redis + Langfuse)
4. **Open WebUI** (syncWave: 30) - Frontend (depends on LiteLLM)

## Verification

### Check Application Status

```bash
# Via ArgoCD CLI
argocd app list
argocd app get redis
argocd app get langfuse
argocd app get litellm
argocd app get open-webui

# Via kubectl
kubectl get applications -n argocd
```

### Check Pod Status

```bash
kubectl get pods -n redis
kubectl get pods -n langfuse
kubectl get pods -n litellm
kubectl get pods -n open-webui
```

### Check Ingress

```bash
kubectl get ingress -n langfuse
kubectl get ingress -n litellm
kubectl get ingress -n open-webui
```

### Test Endpoints

```bash
# Langfuse health check
curl https://langfuse.internal.yourdomain.com/api/public/health

# LiteLLM health check
curl https://gateway.internal.yourdomain.com/health

# Open WebUI health check
curl https://chat.internal.yourdomain.com/health
```

## Troubleshooting

### Application Not Syncing

```bash
# Check sync status
argocd app get <app-name>

# Force sync
argocd app sync <app-name> --force

# Check logs
kubectl logs -n argocd -l app.kubernetes.io/name=argocd-application-controller
```

### Pod Not Starting

```bash
# Check pod events
kubectl describe pod <pod-name> -n <namespace>

# Check logs
kubectl logs <pod-name> -n <namespace>

# Check secrets
kubectl get secret llmops-apikeys-secret -n <namespace>
```

### Ingress Not Working

```bash
# Check ALB controller logs
kubectl logs -n kube-system -l app.kubernetes.io/name=aws-load-balancer-controller

# Check ingress events
kubectl describe ingress <ingress-name> -n <namespace>

# Verify security groups allow traffic
```

### Database Connection Issues

```bash
# Test database connectivity from pod
kubectl exec -it <pod-name> -n <namespace> -- sh
# Inside pod:
psql $DATABASE_URL -c "SELECT 1"
```

## Maintenance

### Update Application

1. Update values in `argocd/helm-values/<app>-values.yaml`
2. Commit and push to Git
3. ArgoCD will auto-sync (or manually sync via UI/CLI)

### Rollback Application

```bash
# Via ArgoCD CLI
argocd app rollback <app-name> <revision>

# Via kubectl (revert Git commit)
git revert <commit-hash>
git push
```

### Scale Application

Update `replicaCount` in the respective values file and commit.

## Security Considerations

1. **Secrets Management**: All secrets are managed via External Secrets Operator
2. **Network Policies**: Consider enabling network policies for pod-to-pod communication
3. **TLS**: All ingresses should use TLS certificates (managed by Cert Manager)
4. **RBAC**: ArgoCD AppProject restricts allowed resources and namespaces
5. **Security Context**: All pods run as non-root with dropped capabilities

## Monitoring

### Metrics

All applications expose Prometheus metrics:
- Redis: `:9121/metrics`
- Langfuse: `:3000/api/public/metrics`
- LiteLLM: `:4000/metrics`

### Logs

All applications use JSON logging for structured log aggregation.

```bash
# View logs
kubectl logs -f <pod-name> -n <namespace>

# View logs from all pods in namespace
kubectl logs -f -l app.kubernetes.io/name=<app-name> -n <namespace>
```

## Cost Optimization

1. **Redis**: Using standalone mode instead of cluster saves resources
2. **Persistence**: Using gp3 EBS volumes for better price/performance
3. **Autoscaling**: Disabled by default, enable when needed
4. **Resource Limits**: Set appropriately to prevent over-provisioning

## Support

For issues or questions:
1. Check ArgoCD application status and events
2. Review pod logs
3. Check External Secrets sync status
4. Verify AWS Secrets Manager values
5. Review ALB controller logs for ingress issues
