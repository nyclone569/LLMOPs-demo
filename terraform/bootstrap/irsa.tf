# IRSA Helper Locals
locals {
  oidc_provider     = data.terraform_remote_state.eks.outputs.oidc_provider
  oidc_provider_arn = data.terraform_remote_state.eks.outputs.oidc_provider_arn
  cluster_name      = data.terraform_remote_state.eks.outputs.cluster_name
  account_id        = data.aws_caller_identity.current.account_id
}

# Helper function to create IRSA trust policy
locals {
  # Function to create IRSA assume role policy
  create_irsa_trust_policy = {
    for key, config in {
      aws_load_balancer_controller = {
        namespace      = "kube-system"
        service_account = "aws-load-balancer-controller"
      }
      external_secrets = {
        namespace      = "external-secrets"
        service_account = "external-secrets"
      }
    } : key => jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect = "Allow"
        Principal = {
          Federated = local.oidc_provider_arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.oidc_provider}:sub" = "system:serviceaccount:${config.namespace}:${config.service_account}"
            "${local.oidc_provider}:aud" = "sts.amazonaws.com"
          }
        }
      }]
    })
  }
}
