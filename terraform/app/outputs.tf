# Cluster information
output "cluster_name" {
  description = "EKS cluster name"
  value       = local.cluster_name
}

output "vpc_id" {
  description = "VPC ID"
  value       = local.vpc_id
}
