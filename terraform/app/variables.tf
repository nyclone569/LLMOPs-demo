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

# External Secrets Configuration
variable "secrets_namespace" {
  description = "Namespace for application secrets"
  type        = string
  default     = "default"
}

variable "supabase_secret_name" {
  description = "AWS Secrets Manager secret name for Supabase credentials"
  type        = string
  default     = "llmops/supabase"
}

variable "api_keys_secret_name" {
  description = "AWS Secrets Manager secret name for API keys"
  type        = string
  default     = "llmops/apikeys"
}
