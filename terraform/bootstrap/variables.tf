variable "aws_region" {
  description = "AWS region for resources"
  type        = string
  default     = "ap-southeast-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "llmops-platform"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

# AWS Load Balancer Controller
variable "aws_load_balancer_controller_version" {
  description = "AWS Load Balancer Controller Helm chart version"
  type        = string
  default     = "1.6.2"
}

# External Secrets Operator
variable "external_secrets_version" {
  description = "External Secrets Operator Helm chart version"
  type        = string
  default     = "0.9.11"
}

# Cert-Manager
variable "cert_manager_version" {
  description = "Cert-Manager Helm chart version"
  type        = string
  default     = "v1.13.3"
}

# Metrics Server
variable "metrics_server_version" {
  description = "Metrics Server Helm chart version"
  type        = string
  default     = "3.11.0"
}

# ArgoCD
variable "argocd_version" {
  description = "ArgoCD Helm chart version"
  type        = string
  default     = "5.51.6"
}

variable "argocd_admin_password" {
  description = "ArgoCD admin password (bcrypt hashed)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "argocd_ingress_enabled" {
  description = "Enable Ingress for ArgoCD"
  type        = bool
  default     = true
}

variable "argocd_ingress_host" {
  description = "Hostname for ArgoCD Ingress"
  type        = string
  default     = "argocd.example.com"
}
