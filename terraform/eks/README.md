# EKS Module - Layer 2

This is the second layer of the 4-layer Terraform stack (vpc → eks → bootstrap → app). It creates a production-ready EKS cluster with separate node groups for system and workload components.

## Architecture

```
EKS Cluster (llmops-cluster)
├── Control Plane (Kubernetes 1.29)
│   ├── Private endpoint: enabled
│   ├── Public endpoint: enabled
│   └── IRSA (OIDC): enabled
├── System Node Group (t3.medium x2)
│   ├── Role: kube-system, addons
│   ├── Taint: CriticalAddonsOnly=true:NoSchedule
│   └── Labels: role=system
└── Workload Node Group (t3.large x2)
    ├── Role: LiteLLM, Langfuse, apps
    └── Labels: role=workload
```

## Features

- **Multi-Node Groups**: Separate system and workload nodes
- **IRSA Enabled**: IAM Roles for Service Accounts via OIDC
- **Cluster Add-ons**: CoreDNS, kube-proxy, VPC CNI, Pod Identity Agent
- **Auto-scaling Ready**: Tagged for Cluster Autoscaler
- **Private Networking**: Nodes in private subnets
- **CloudWatch Logging**: Full control plane logging
- **aws-auth**: Terraform executor has cluster access

## Prerequisites

1. **VPC Layer**: Must be deployed first
   ```bash
   cd ../vpc && terraform apply
   ```

2. **AWS CLI** configured with appropriate credentials
3. **kubectl** installed
4. **Terraform** >= 1.5

## Usage

### 1. Configure Variables

Copy the example file and customize:
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

This takes approximately 15-20 minutes.

### 5. Configure kubectl

```bash
aws eks update-kubeconfig --region ap-southeast-1 --name llmops-cluster
```

### 6. Verify Cluster

```bash
# Check nodes
kubectl get nodes

# Check system pods
kubectl get pods -n kube-system

# Check node labels
kubectl get nodes --show-labels

# Check node taints
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints
```

## Variables

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| aws_region | AWS region | string | ap-southeast-1 | no |
| project_name | Project name | string | llmops-platform | no |
| environment | Environment | string | dev | no |
| cluster_name | EKS cluster name | string | llmops-cluster | no |
| kubernetes_version | K8s version | string | 1.29 | no |
| system_node_group_instance_types | System node types | list(string) | ["t3.medium"] | no |
| system_node_group_desired_size | System desired nodes | number | 2 | no |
| workload_node_group_instance_types | Workload node types | list(string) | ["t3.large"] | no |
| workload_node_group_desired_size | Workload desired nodes | number | 2 | no |

## Outputs

These outputs are consumed by downstream layers (bootstrap, app):

| Name | Description |
|------|-------------|
| cluster_name | EKS cluster name |
| cluster_endpoint | API server endpoint |
| cluster_certificate_authority_data | CA cert (sensitive) |
| oidc_provider_arn | OIDC provider ARN (for IRSA) |
| oidc_provider | OIDC provider URL |
| node_security_group_id | Node security group ID |
| cluster_primary_security_group_id | Cluster security group ID |

## Downstream Layer Integration

Bootstrap layer references this EKS cluster:

```hcl
data "terraform_remote_state" "eks" {
  backend = "s3"
  config = {
    bucket = "llmops-tfstate"
    key    = "eks/terraform.tfstate"
    region = "ap-southeast-1"
  }
}

# Usage
cluster_name       = data.terraform_remote_state.eks.outputs.cluster_name
oidc_provider_arn  = data.terraform_remote_state.eks.outputs.oidc_provider_arn
```

## Node Groups

### System Node Group
- **Purpose**: kube-system pods, cluster addons
- **Instance Type**: t3.medium (2 vCPU, 4 GB RAM)
- **Taint**: `CriticalAddonsOnly=true:NoSchedule`
- **Labels**: `role=system`
- **Pods**: CoreDNS, kube-proxy, VPC CNI, metrics-server, etc.

To schedule on system nodes:
```yaml
tolerations:
- key: "CriticalAddonsOnly"
  operator: "Equal"
  value: "true"
  effect: "NoSchedule"
nodeSelector:
  role: system
```

### Workload Node Group
- **Purpose**: Application workloads (LiteLLM, Langfuse, etc.)
- **Instance Type**: t3.large (2 vCPU, 8 GB RAM)
- **Labels**: `role=workload`
- **No taints**: Regular workloads schedule here by default

To schedule on workload nodes:
```yaml
nodeSelector:
  role: workload
```

## IRSA (IAM Roles for Service Accounts)

OIDC provider is enabled for IRSA. Example usage:

```hcl
# Create IAM role for a service account
resource "aws_iam_role" "external_secrets" {
  name = "external-secrets-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRoleWithWebIdentity"
      Effect = "Allow"
      Principal = {
        Federated = data.terraform_remote_state.eks.outputs.oidc_provider_arn
      }
      Condition = {
        StringEquals = {
          "${data.terraform_remote_state.eks.outputs.oidc_provider}:sub" = "system:serviceaccount:external-secrets:external-secrets"
        }
      }
    }]
  })
}
```

## Cost Estimate

**Monthly costs (ap-southeast-1):**
- EKS Control Plane: $73/month
- System nodes (t3.medium x2): ~$60/month
- Workload nodes (t3.large x2): ~$120/month
- Data transfer: Variable
- **Total**: ~$250-280/month

## Troubleshooting

### Nodes not joining cluster

```bash
# Check node group status
aws eks describe-nodegroup \
  --cluster-name llmops-cluster \
  --nodegroup-name llmops-cluster-system \
  --region ap-southeast-1

# Check IAM role
aws iam get-role --role-name llmops-cluster-node-group-role
```

### Cannot connect to cluster

```bash
# Update kubeconfig
aws eks update-kubeconfig --region ap-southeast-1 --name llmops-cluster

# Verify AWS identity
aws sts get-caller-identity

# Check aws-auth configmap
kubectl get configmap aws-auth -n kube-system -o yaml
```

### Pods stuck in Pending

```bash
# Check node capacity
kubectl describe nodes

# Check pod events
kubectl describe pod <pod-name>

# Check if taint is blocking scheduling
kubectl get nodes -o custom-columns=NAME:.metadata.name,TAINTS:.spec.taints
```

## Cleanup

```bash
terraform destroy
```

**Warning**: Ensure no applications are running in the cluster before destroying.

## Next Steps

After EKS is created, proceed to Layer 3 (bootstrap):

```bash
cd ../bootstrap
```

The bootstrap layer will install:
- AWS Load Balancer Controller
- External Secrets Operator
- Metrics Server
- Cluster Autoscaler
