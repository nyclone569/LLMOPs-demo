# AWS Load Balancer Controller outputs
output "aws_load_balancer_controller_role_arn" {
  description = "ARN of IAM role for AWS Load Balancer Controller"
  value       = aws_iam_role.aws_load_balancer_controller.arn
}

output "aws_load_balancer_controller_role_name" {
  description = "Name of IAM role for AWS Load Balancer Controller"
  value       = aws_iam_role.aws_load_balancer_controller.name
}

# External Secrets Operator outputs
output "external_secrets_role_arn" {
  description = "ARN of IAM role for External Secrets Operator"
  value       = aws_iam_role.external_secrets.arn
}

output "external_secrets_role_name" {
  description = "Name of IAM role for External Secrets Operator"
  value       = aws_iam_role.external_secrets.name
}

output "external_secrets_namespace" {
  description = "Namespace for External Secrets Operator"
  value       = kubernetes_namespace.external_secrets.metadata[0].name
}

# Cert-Manager outputs
output "cert_manager_namespace" {
  description = "Namespace for Cert-Manager"
  value       = kubernetes_namespace.cert_manager.metadata[0].name
}

# ArgoCD outputs
output "argocd_namespace" {
  description = "Namespace for ArgoCD"
  value       = kubernetes_namespace.argocd.metadata[0].name
}

output "argocd_admin_password" {
  description = "ArgoCD admin password"
  value       = local.argocd_admin_password
  sensitive   = true
}

output "argocd_server_url" {
  description = "ArgoCD server URL"
  value       = var.argocd_ingress_enabled ? "https://${var.argocd_ingress_host}" : "Check LoadBalancer service"
}

# Cluster information
output "cluster_name" {
  description = "EKS cluster name"
  value       = local.cluster_name
}

output "oidc_provider_arn" {
  description = "OIDC provider ARN"
  value       = local.oidc_provider_arn
}

# Analytics IRSA outputs
output "analytics_open_webui_role_arn" {
  description = "ARN of IAM role for Open WebUI pod (analytics S3 access)"
  value       = aws_iam_role.analytics_open_webui.arn
}

output "analytics_s3_bucket" {
  description = "S3 bucket name for NYC taxi analytics Parquet data"
  value       = aws_s3_bucket.analytics.id
}
