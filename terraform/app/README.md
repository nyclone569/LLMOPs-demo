# App Layer - Layer 4

This is the fourth and final layer of the 4-layer Terraform stack (vpc → eks → bootstrap → app). It creates application infrastructure including ElastiCache Redis and manages application secrets via External Secrets Operator.

## Architecture

```
App Layer
├── ElastiCache Redis (cache.t3.micro)
│   ├── Subnet group (private subnets)
│   ├── Security group (EKS nodes only)
│   └── Parameter group (optimized for caching)
│
└── External Secrets
    ├── llmops-supabase-secret (Supabase credentials)
    ├── llmops-apikeys-secret (API keys)
    └── llmops-redis-secret (Redis connection)
```

## Components

### 1. ElastiCache Redis
- **Purpose**: Caching layer for LiteLLM and applications
- **Instance Type**: cache.t3.micro (512 MB memory)
- **Engine**: Redis 7.0
- **Mode**: Non-clustered (single node)
- **Network**: Private subnets only
- **Access**: EKS nodes only via security group

### 2. External Secrets
- **Supabase Secret**: Database credentials and connection strings
- **API Keys Secret**: OpenAI, Anthropic, Langfuse keys
- **Redis Secret**: Redis connection details (auto-generated)

## Prerequisites

1. **VPC Layer**: Must be deployed
2. **EKS Layer**: Must be deployed
3. **Bootstrap Layer**: Must be deployed (External Secrets Operator required)
4. **AWS Secrets Manager**: Secrets must be created before deployment

## Pre-Deployment: Create AWS Secrets

### Create Supabase Secret

```bash
aws secretsmanager create-secret \
  --name llmops/supabase \
  --description "Supabase credentials for LLMOps platform" \
  --secret-string '{
    "supabase_url": "https://your-project.supabase.co",
    "supabase_anon_key": "your-anon-key",
    "supabase_service_key": "your-service-key",
    "database_url": "postgresql://postgres:password@db.your-project.supabase.co:5432/postgres"
  }' \
  --region ap-southeast-1
```

### Create API Keys Secret

```bash
aws secretsmanager create-secret \
  --name llmops/api-keys \
  --description "API keys for LLMOps platform" \
  --secret-string '{
    "openai_api_key": "sk-...",
    "anthropic_api_key": "sk-ant-...",
    "langfuse_public_key": "pk-lf-...",
    "langfuse_secret_key": "sk-lf-..."
  }' \
  --region ap-southeast-1
```

## Usage

### 1. Configure Variables

```bash
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
```

### 2. Initialize Terraform

```bash
terraform init
```

### 3. Plan

```bash
terraform plan
```

### 4. Apply

```bash
terraform apply
```

This takes approximately 5-10 minutes (ElastiCache creation).

### 5. Verify Resources

```bash
# Check ElastiCache cluster
aws elasticache describe-cache-clusters \
  --cache-cluster-id llmops-platform-dev-redis \
  --region ap-southeast-1

# Check Kubernetes secrets
kubectl get externalsecret -n default
kubectl get secret llmops-supabase-secret -n default
kubectl get secret llmops-apikeys-secret -n default
kubectl get secret llmops-redis-secret -n default

# Verify secret data (base64 encoded)
kubectl get secret llmops-redis-secret -n default -o yaml
```

## Variables

| Name | Description | Type | Default |
|------|-------------|------|---------|
| aws_region | AWS region | string | ap-southeast-1 |
| project_name | Project name | string | llmops-platform |
| environment | Environment | string | dev |
| redis_node_type | Redis instance type | string | cache.t3.micro |
| redis_num_cache_nodes | Number of nodes | number | 1 |
| redis_engine_version | Redis version | string | 7.0 |
| secrets_namespace | K8s namespace for secrets | string | default |
| supabase_secret_name | AWS secret name | string | llmops/supabase |
| api_keys_secret_name | AWS secret name | string | llmops/api-keys |

## Outputs

| Name | Description |
|------|-------------|
| redis_endpoint | Redis endpoint (host:port) |
| redis_host | Redis hostname |
| redis_port | Redis port |
| redis_cluster_id | ElastiCache cluster ID |
| supabase_secret_name | K8s secret name for Supabase |
| api_keys_secret_name | K8s secret name for API keys |
| redis_secret_name | K8s secret name for Redis |

## Using Secrets in Applications

### Example: LiteLLM Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: litellm
spec:
  template:
    spec:
      containers:
      - name: litellm
        image: ghcr.io/berriai/litellm:latest
        env:
        # From Supabase secret
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: llmops-supabase-secret
              key: DATABASE_URL
        # From API keys secret
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: llmops-apikeys-secret
              key: OPENAI_API_KEY
        # From Redis secret
        - name: REDIS_HOST
          valueFrom:
            secretKeyRef:
              name: llmops-redis-secret
              key: REDIS_HOST
        - name: REDIS_PORT
          valueFrom:
            secretKeyRef:
              name: llmops-redis-secret
              key: REDIS_PORT
```

### Example: Langfuse Deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: langfuse
spec:
  template:
    spec:
      containers:
      - name: langfuse
        image: langfuse/langfuse:latest
        env:
        # From Supabase secret
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: llmops-supabase-secret
              key: DATABASE_URL
        # From API keys secret (for Langfuse auth)
        - name: LANGFUSE_PUBLIC_KEY
          valueFrom:
            secretKeyRef:
              name: llmops-apikeys-secret
              key: LANGFUSE_PUBLIC_KEY
        - name: LANGFUSE_SECRET_KEY
          valueFrom:
            secretKeyRef:
              name: llmops-apikeys-secret
              key: LANGFUSE_SECRET_KEY
```

## ElastiCache Redis Details

### Configuration
- **Engine**: Redis 7.0
- **Node Type**: cache.t3.micro (512 MB memory, 2 vCPUs)
- **Nodes**: 1 (non-clustered)
- **Eviction Policy**: allkeys-lru (evict least recently used keys)
- **Timeout**: 300 seconds
- **Maintenance Window**: Sunday 05:00-06:00 UTC
- **Backup Window**: 03:00-04:00 UTC
- **Backup Retention**: 1 day

### Network Security
- **Subnets**: Private subnets only (no public access)
- **Security Group**: Allows port 6379 from EKS nodes only
- **Encryption**: In-transit encryption available (not enabled by default)

### Connection String
```
redis://<redis_host>:6379
```

Get from Terraform output:
```bash
terraform output redis_endpoint
```

## External Secrets Details

### How It Works

1. **AWS Secrets Manager**: Stores actual secrets
2. **External Secrets Operator**: Syncs secrets to Kubernetes
3. **ClusterSecretStore**: Configured in bootstrap layer
4. **ExternalSecret CRDs**: Define which secrets to sync
5. **Kubernetes Secrets**: Created automatically by operator

### Secret Refresh

Secrets are automatically refreshed every 1 hour. To force refresh:

```bash
# Delete the Kubernetes secret (will be recreated)
kubectl delete secret llmops-supabase-secret -n default

# Or restart the External Secrets Operator
kubectl rollout restart deployment external-secrets -n external-secrets
```

### Updating Secrets

Update in AWS Secrets Manager:

```bash
aws secretsmanager update-secret \
  --secret-id llmops/supabase \
  --secret-string '{
    "supabase_url": "https://new-project.supabase.co",
    "supabase_anon_key": "new-anon-key",
    "supabase_service_key": "new-service-key",
    "database_url": "postgresql://postgres:newpass@db.new-project.supabase.co:5432/postgres"
  }' \
  --region ap-southeast-1
```

Wait up to 1 hour for automatic sync, or force refresh as shown above.

## Troubleshooting

### ElastiCache Issues

**Problem**: Cannot connect to Redis from pods

```bash
# Check security group rules
aws ec2 describe-security-groups \
  --group-ids $(terraform output -raw redis_security_group_id) \
  --region ap-southeast-1

# Check Redis cluster status
aws elasticache describe-cache-clusters \
  --cache-cluster-id llmops-platform-dev-redis \
  --show-cache-node-info \
  --region ap-southeast-1

# Test connection from a pod
kubectl run redis-test --rm -it --image=redis:7-alpine -- redis-cli -h <redis_host> -p 6379 ping
```

**Problem**: Redis cluster creation failed

```bash
# Check CloudWatch logs
aws logs tail /aws/elasticache/llmops-platform-dev-redis --follow

# Check subnet group
aws elasticache describe-cache-subnet-groups \
  --cache-subnet-group-name llmops-platform-dev-redis-subnet-group
```

### External Secrets Issues

**Problem**: ExternalSecret not syncing

```bash
# Check ExternalSecret status
kubectl describe externalsecret llmops-supabase-secret -n default

# Check External Secrets Operator logs
kubectl logs -n external-secrets deployment/external-secrets

# Check ClusterSecretStore
kubectl get clustersecretstore aws-secrets-manager -o yaml

# Verify IAM permissions
aws secretsmanager get-secret-value \
  --secret-id llmops/supabase \
  --region ap-southeast-1
```

**Problem**: Secret exists but data is empty

```bash
# Check secret data
kubectl get secret llmops-supabase-secret -n default -o jsonpath='{.data}' | jq

# Check if AWS secret has correct format
aws secretsmanager get-secret-value \
  --secret-id llmops/supabase \
  --region ap-southeast-1 \
  --query SecretString \
  --output text | jq
```

**Problem**: Secret not found in AWS

```bash
# List all secrets
aws secretsmanager list-secrets --region ap-southeast-1

# Create missing secret
aws secretsmanager create-secret \
  --name llmops/supabase \
  --secret-string '{"key":"value"}' \
  --region ap-southeast-1
```

## Cost Estimate

### Monthly Costs (ap-southeast-1)

| Resource | Configuration | Cost |
|----------|---------------|------|
| ElastiCache Redis | cache.t3.micro x1 | ~$15/month |
| Data transfer | Variable | ~$5/month |
| Secrets Manager | 2 secrets | ~$0.80/month |
| **Total** | | **~$21/month** |

### Cost Optimization

- **Dev/Staging**: Use cache.t3.micro (current)
- **Production**: Consider cache.t3.small or cache.t3.medium
- **High Availability**: Enable Multi-AZ (doubles cost)
- **Backup**: Reduce retention period if not needed

## Cleanup

```bash
# Delete Kubernetes secrets first
kubectl delete externalsecret llmops-supabase-secret -n default
kubectl delete externalsecret llmops-apikeys-secret -n default
kubectl delete secret llmops-redis-secret -n default

# Destroy infrastructure
terraform destroy
```

**Warning**: This will delete the Redis cluster and all cached data.

## Next Steps

After app layer is deployed:

1. **Deploy LiteLLM**
   - Use secrets created by this layer
   - Configure Redis caching
   - Set up Ingress

2. **Deploy Langfuse**
   - Use Supabase connection
   - Configure authentication
   - Set up Ingress

3. **Configure Monitoring**
   - CloudWatch metrics for ElastiCache
   - Kubernetes metrics for pods
   - Application-level monitoring

4. **Set Up CI/CD**
   - ArgoCD applications
   - GitOps workflow
   - Automated deployments

## Security Best Practices

1. **Secrets Management**
   - ✅ Secrets stored in AWS Secrets Manager
   - ✅ IRSA for External Secrets Operator
   - ✅ Automatic secret rotation supported
   - ✅ Secrets never in Terraform state

2. **Network Security**
   - ✅ Redis in private subnets only
   - ✅ Security group restricts access to EKS nodes
   - ✅ No public endpoints

3. **Encryption**
   - ✅ Secrets encrypted at rest in AWS
   - ✅ Secrets encrypted in Kubernetes etcd
   - ⚠️ Redis in-transit encryption not enabled (enable for production)

## References

- [ElastiCache for Redis](https://docs.aws.amazon.com/AmazonElastiCache/latest/red-ug/)
- [External Secrets Operator](https://external-secrets.io/)
- [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/)
- [Supabase Documentation](https://supabase.com/docs)
