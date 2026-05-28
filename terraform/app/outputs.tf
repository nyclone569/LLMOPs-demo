# ElastiCache Redis outputs
output "redis_endpoint" {
  description = "ElastiCache Redis endpoint"
  value       = "${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.port}"
}

output "redis_host" {
  description = "ElastiCache Redis host"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "redis_port" {
  description = "ElastiCache Redis port"
  value       = aws_elasticache_cluster.redis.port
}

output "redis_cluster_id" {
  description = "ElastiCache Redis cluster ID"
  value       = aws_elasticache_cluster.redis.cluster_id
}

output "redis_security_group_id" {
  description = "Security group ID for Redis cluster"
  value       = aws_security_group.redis.id
}

# External Secrets outputs
output "supabase_secret_name" {
  description = "Kubernetes secret name for Supabase credentials"
  value       = kubernetes_manifest.external_secret_supabase.manifest.metadata.name
}

output "api_keys_secret_name" {
  description = "Kubernetes secret name for API keys"
  value       = kubernetes_manifest.external_secret_api_keys.manifest.metadata.name
}

output "redis_secret_name" {
  description = "Kubernetes secret name for Redis connection"
  value       = kubernetes_secret.redis_connection.metadata[0].name
}

# Cluster information
output "cluster_name" {
  description = "EKS cluster name"
  value       = local.cluster_name
}

output "vpc_id" {
  description = "VPC ID"
  value       = local.vpc_id
}
