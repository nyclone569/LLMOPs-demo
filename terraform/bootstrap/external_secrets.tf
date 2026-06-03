# IAM Policy for External Secrets Operator
resource "aws_iam_policy" "external_secrets" {
  name        = "${local.cluster_name}-external-secrets"
  description = "IAM policy for External Secrets Operator to access AWS Secrets Manager"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:DescribeSecret",
          "secretsmanager:ListSecrets"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${local.account_id}:secret:llmops/*"
      },
      {
        Effect = "Allow"
        Action = [
          "secretsmanager:ListSecrets"
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "${local.cluster_name}-external-secrets"
  }
}

# IAM Role for External Secrets Operator
resource "aws_iam_role" "external_secrets" {
  name = "${local.cluster_name}-external-secrets"

  assume_role_policy = local.create_irsa_trust_policy["external_secrets"]

  tags = {
    Name = "${local.cluster_name}-external-secrets"
  }
}

# Attach policy to role
resource "aws_iam_role_policy_attachment" "external_secrets" {
  policy_arn = aws_iam_policy.external_secrets.arn
  role       = aws_iam_role.external_secrets.name
}

# Helm release for External Secrets Operator
resource "helm_release" "external_secrets" {
  name       = "external-secrets"
  repository = "https://charts.external-secrets.io"
  chart      = "external-secrets"
  version    = var.external_secrets_version
  namespace  = kubernetes_namespace.external_secrets.metadata[0].name

  set {
    name  = "installCRDs"
    value = "true"
  }

  set {
    name  = "serviceAccount.create"
    value = "true"
  }

  set {
    name  = "serviceAccount.name"
    value = "external-secrets"
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.external_secrets.arn
  }

  depends_on = [
    kubernetes_namespace.external_secrets,
    aws_iam_role_policy_attachment.external_secrets,
    helm_release.aws_load_balancer_controller
  ]
}

# Use null_resource + kubectl to avoid CRD pre-validation at plan time
resource "null_resource" "cluster_secret_store_aws" {
  triggers = {
    chart_version = helm_release.external_secrets.version
    region        = var.aws_region
  }

  provisioner "local-exec" {
    command = <<-EOF
      aws eks update-kubeconfig --region ${var.aws_region} --name ${local.cluster_name}
      until kubectl get crd clustersecretstores.external-secrets.io >/dev/null 2>&1; do
        echo "Waiting for ClusterSecretStore CRD..."; sleep 5
      done
      kubectl apply -f - <<YAML
apiVersion: external-secrets.io/v1beta1
kind: ClusterSecretStore
metadata:
  name: aws-secrets-manager
spec:
  provider:
    aws:
      service: SecretsManager
      region: ${var.aws_region}
      auth:
        jwt:
          serviceAccountRef:
            name: external-secrets
            namespace: ${kubernetes_namespace.external_secrets.metadata[0].name}
YAML
    EOF
  }

  depends_on = [helm_release.external_secrets]
}
