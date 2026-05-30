# Ensure the AWS Secrets Manager secrets exist with the right structure.
# Actual values must be populated manually via console or aws CLI before
# ArgoCD ExternalSecrets can sync them into Kubernetes.

resource "aws_secretsmanager_secret" "apikeys" {
  name                    = var.api_keys_secret_name
  description             = "LLMOps platform API keys — populated manually"
  recovery_window_in_days = 0

  tags = {
    Name      = var.api_keys_secret_name
    ManagedBy = "Terraform"
  }
}

resource "aws_secretsmanager_secret" "supabase" {
  name                    = var.supabase_secret_name
  description             = "LLMOps Supabase DB connection strings — populated manually"
  recovery_window_in_days = 0

  tags = {
    Name      = var.supabase_secret_name
    ManagedBy = "Terraform"
  }
}
