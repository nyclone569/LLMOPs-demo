# Cluster outputs
output "cluster_name" {
  description = "Name of the EKS cluster"
  value       = module.eks.cluster_name
}

output "cluster_id" {
  description = "ID of the EKS cluster"
  value       = module.eks.cluster_id
}

output "cluster_arn" {
  description = "ARN of the EKS cluster"
  value       = module.eks.cluster_arn
}

output "cluster_endpoint" {
  description = "Endpoint for EKS control plane"
  value       = module.eks.cluster_endpoint
}

output "cluster_version" {
  description = "Kubernetes version of the cluster"
  value       = module.eks.cluster_version
}

output "cluster_certificate_authority_data" {
  description = "Base64 encoded certificate data required to communicate with the cluster"
  value       = module.eks.cluster_certificate_authority_data
  sensitive   = true
}

# OIDC Provider outputs (for IRSA)
output "oidc_provider" {
  description = "OIDC provider URL without https://"
  value       = module.eks.oidc_provider
}

output "oidc_provider_arn" {
  description = "ARN of the OIDC Provider for EKS"
  value       = module.eks.oidc_provider_arn
}

output "cluster_oidc_issuer_url" {
  description = "The URL on the EKS cluster OIDC Issuer"
  value       = module.eks.cluster_oidc_issuer_url
}

# Security Group outputs
output "cluster_security_group_id" {
  description = "Security group ID attached to the EKS cluster"
  value       = module.eks.cluster_security_group_id
}

output "cluster_primary_security_group_id" {
  description = "Cluster primary security group ID"
  value       = module.eks.cluster_primary_security_group_id
}

output "node_security_group_id" {
  description = "Security group ID attached to the EKS nodes"
  value       = module.eks.node_security_group_id
}

# Node Group outputs
output "system_node_group_id" {
  description = "System node group ID"
  value       = aws_eks_node_group.system.id
}

output "system_node_group_arn" {
  description = "System node group ARN"
  value       = aws_eks_node_group.system.arn
}

output "system_node_group_status" {
  description = "System node group status"
  value       = aws_eks_node_group.system.status
}

output "workload_node_group_id" {
  description = "Workload node group ID"
  value       = aws_eks_node_group.workload.id
}

output "workload_node_group_arn" {
  description = "Workload node group ARN"
  value       = aws_eks_node_group.workload.arn
}

output "workload_node_group_status" {
  description = "Workload node group status"
  value       = aws_eks_node_group.workload.status
}

output "node_group_role_arn" {
  description = "IAM role ARN for node groups"
  value       = aws_iam_role.node_group.arn
}

output "node_group_role_name" {
  description = "IAM role name for node groups"
  value       = aws_iam_role.node_group.name
}

# kubectl configuration
output "configure_kubectl" {
  description = "Command to configure kubectl"
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

# Cluster add-ons
output "cluster_addons" {
  description = "Map of cluster addon attributes"
  value       = module.eks.cluster_addons
}
