# Bootstrap Module - Layer 3

This is the third layer of the 4-layer Terraform stack (vpc → eks → bootstrap → app). It installs essential cluster add-ons and operators using Helm with proper IRSA configurations.

## Architecture

```
Bootstrap Layer
├── AWS Load Balancer Controller (kube-system)
│   └── IRSA role with ELB permissions
├── External Secrets Operator (external-secrets)
│   ├── IRSA role with Secrets Manager permissions
│   └── ClusterSecretStore for AWS Secrets Manager
├── Cert-Manager (cert-manager)
│   ├── Let's Encrypt staging issuer
│   └── Let's Encrypt production issuer
├── Metrics Server (kube-system)
│   └── For HPA and kubectl top
└── ArgoCD (argocd)
    ├── Ingress with ALB
    └── Admin password management
```

## Components

### 1. AWS Load Balancer Controller
- **Purpose**: Manages AWS ALB/NLB for Kubernetes Ingress
- **Namespace**: kube-system
- **IRSA**: Yes (full ELB permissions)
- **Chart**: eks/aws-load-balancer-controller

### 2. External Secrets Operator
- **Purpose**: Syncs secrets from AWS Secrets Manager to Kubernetes
- **Namespace**: external-secrets
- **IRSA**: Yes (Secrets Manager read permissions)
- **Chart**: external-secrets/external-secrets
- **Includes**: ClusterSecretStore for AWS Secrets Manager

### 3. Cert-Manager
- **Purpose**: Automates TLS certificate management
- **Namespace**: cert-manager
- **IRSA**: No
- **Chart**: jetstack/cert-manager
- **Includes**: Let's Encrypt ClusterIssuers (staging + prod)

### 4. Metrics Server
- **Purpose**: Provides resource metrics for HPA and kubectl top
- **Namespace**: kube-system
- **IRSA**: No
- **Chart**: metrics-server/metrics-server

### 5. ArgoCD
- **Purpose**: GitOps continuous delivery
- **Namespace**: argocd
- **IRSA**: No
- **Chart**: argo/argo-cd
- **Access**: Via Ingress (ALB) or LoadBalancer

## Prerequisites

1. **VPC Layer**: Must be deployed
2. **EKS Layer**: Must be deployed and accessible
3. **kubectl**: Configured for the cluster
4. **Helm**: Not required locally (Terraform manages it)

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

This takes approximately 5-10 minutes.

### 5. Verify Installations

```bash
# Check all pods
kubectl get pods -A

# AWS Load Balancer Controller
kubectl get deployment -n kube-system aws-load-balancer-controller

# External Secrets Operator
kubectl get deployment -n external-secrets external-secrets
kubectl get clustersecretstore

# Cert-Manager
kubectl get deployment -n cert-manager cert-manager
kubectl get clusterissuer

# Metrics Server
kubectl get deployment -n kube-system metrics-server
kubectl top nodes

# ArgoCD
kubectl get deployment -n argocd
kubectl get ingress -n argocd
```

## Variables

| Name | Description | Type | Default |
|------|-------------|------|---------|
| aws_region | AWS region | string | ap-southeast-1 |
| project_name | Project name | string | llmops-platform |
| environment | Environment | string | dev |
| aws_load_balancer_controller_version | ALB controller version | string | 1.6.2 |
| external_secrets_version | External Secrets version | string | 0.9.11 |
| cert_manager_version | Cert-Manager version | string | v1.13.3 |
| metrics_server_version | Metrics Server version | string | 3.11.0 |
| argocd_version | ArgoCD version | string | 5.51.6 |
| argocd_admin_password | ArgoCD admin password | string | "" (auto-generated) |
| argocd_ingress_enabled | Enable ArgoCD Ingress | bool | true |
| argocd_ingress_host | ArgoCD hostname | string | argocd.example.com |

## Outputs

| Name | Description |
|------|-------------|
| aws_load_balancer_controller_role_arn | ALB controller IAM role ARN |
| external_secrets_role_arn | External Secrets IAM role ARN |
| argocd_admin_password | ArgoCD admin password (sensitive) |
| argocd_server_url | ArgoCD server URL |

## Post-Deployment

### Access ArgoCD

Get the admin password:
```bash
terraform output -raw argocd_admin_password
```

If using Ingress:
```bash
# Get Ingress URL
kubectl get ingress -n argocd

# Access via browser
open https://argocd.llmops-platform.com
```

If using LoadBalancer:
```bash
# Get LoadBalancer URL
kubectl get svc -n argocd argocd-server

# Access via browser
open http://<LOAD_BALANCER_URL>
```

Login:
- Username: `admin`
- Password: (from terraform output)

### Test External Secrets

Create a test secret in AWS Secrets Manager:
```bash
aws secretsmanager create-secret \
  --name llmops-platform/test/db-password \
  --secret-string "my-secret-password" \
  --region ap-southeast-1
```

Create an ExternalSecret:
```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: test-secret
  namespace: default
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secrets-manager
    kind: ClusterSecretStore
  target:
    name: test-secret
    creationPolicy: Owner
  data:
  - secretKey: password
    remoteRef:
      key: llmops-platform/test/db-password
```

Verify:
```bash
kubectl get externalsecret test-secret
kubectl get secret test-secret
```

### Test Cert-Manager

Create a test certificate:
```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: test-cert
  namespace: default
spec:
  secretName: test-cert-tls
  issuerRef:
    name: letsencrypt-staging
    kind: ClusterIssuer
  dnsNames:
  - test.llmops-platform.com
```

Verify:
```bash
kubectl get certificate test-cert
kubectl describe certificate test-cert
```

### Test Metrics Server

```bash
# View node metrics
kubectl top nodes

# View pod metrics
kubectl top pods -A
```

## IRSA Roles

### AWS Load Balancer Controller

**Role**: `llmops-cluster-aws-load-balancer-controller`

**Permissions**:
- Full ELB management
- EC2 describe operations
- Security group management
- Target group management

**Service Account**: `aws-load-balancer-controller` (kube-system)

### External Secrets Operator

**Role**: `llmops-cluster-external-secrets`

**Permissions**:
- `secretsmanager:GetSecretValue`
- `secretsmanager:DescribeSecret`
- `secretsmanager:ListSecrets`

**Resource**: `arn:aws:secretsmanager:*:*:secret:llmops-platform/*`

**Service Account**: `external-secrets` (external-secrets)

## Troubleshooting

### AWS Load Balancer Controller not creating ALBs

```bash
# Check controller logs
kubectl logs -n kube-system deployment/aws-load-balancer-controller

# Check IAM role
aws iam get-role --role-name llmops-cluster-aws-load-balancer-controller

# Verify IRSA annotation
kubectl get sa aws-load-balancer-controller -n kube-system -o yaml
```

### External Secrets not syncing

```bash
# Check operator logs
kubectl logs -n external-secrets deployment/external-secrets

# Check ClusterSecretStore
kubectl get clustersecretstore aws-secrets-manager -o yaml

# Check ExternalSecret status
kubectl describe externalsecret <name>

# Verify IAM permissions
aws secretsmanager get-secret-value \
  --secret-id llmops-platform/test/db-password \
  --region ap-southeast-1
```

### Cert-Manager not issuing certificates

```bash
# Check cert-manager logs
kubectl logs -n cert-manager deployment/cert-manager

# Check certificate status
kubectl describe certificate <name>

# Check certificate request
kubectl get certificaterequest
kubectl describe certificaterequest <name>

# Check challenge (for Let's Encrypt)
kubectl get challenge
```

### Metrics Server not working

```bash
# Check metrics-server logs
kubectl logs -n kube-system deployment/metrics-server

# Test metrics API
kubectl get --raw /apis/metrics.k8s.io/v1beta1/nodes
```

### ArgoCD not accessible

```bash
# Check ArgoCD pods
kubectl get pods -n argocd

# Check Ingress
kubectl get ingress -n argocd
kubectl describe ingress -n argocd

# Check ALB
aws elbv2 describe-load-balancers --region ap-southeast-1

# Reset admin password
kubectl -n argocd patch secret argocd-secret \
  -p '{"stringData": {"admin.password": "'$(htpasswd -bnBC 10 "" <new-password> | tr -d ':\n')'"}}'
```

## Cleanup

```bash
terraform destroy
```

**Warning**: This will remove all cluster add-ons. Ensure no applications depend on them.

## Next Steps

After bootstrap is complete, proceed to Layer 4 (app):

```bash
cd ../app
```

The app layer will deploy:
- LiteLLM
- Langfuse
- PostgreSQL
- Redis
- Application-specific resources

## Cost Impact

Bootstrap layer adds minimal cost:
- **AWS Load Balancer Controller**: $0 (manages ALBs created by apps)
- **External Secrets Operator**: $0
- **Cert-Manager**: $0
- **Metrics Server**: $0
- **ArgoCD**: $0

ALBs created by applications will incur costs (~$20/month per ALB).
