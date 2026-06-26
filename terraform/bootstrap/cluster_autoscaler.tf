locals {
  ca_service_account = "cluster-autoscaler"
}

# IAM Policy — minimal permissions for node group discovery and scaling
resource "aws_iam_policy" "cluster_autoscaler" {
  name        = "${local.cluster_name}-cluster-autoscaler"
  description = "Cluster Autoscaler policy for EKS node group scaling"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups",
          "autoscaling:DescribeAutoScalingInstances",
          "autoscaling:DescribeLaunchConfigurations",
          "autoscaling:DescribeScalingActivities",
          "autoscaling:SetDesiredCapacity",
          "autoscaling:TerminateInstanceInAutoScalingGroup",
          "autoscaling:UpdateAutoScalingGroup",
          "ec2:DescribeLaunchTemplateVersions",
          "ec2:DescribeInstanceTypes",
        ]
        Resource = "*"
      }
    ]
  })

  tags = {
    Name = "${local.cluster_name}-cluster-autoscaler"
  }
}

# IRSA Role — bound to kube-system/cluster-autoscaler service account
resource "aws_iam_role" "cluster_autoscaler" {
  name = "${local.cluster_name}-cluster-autoscaler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = local.oidc_provider_arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_provider}:sub" = "system:serviceaccount:kube-system:${local.ca_service_account}"
          "${local.oidc_provider}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = {
    Name = "${local.cluster_name}-cluster-autoscaler"
  }
}

resource "aws_iam_role_policy_attachment" "cluster_autoscaler" {
  policy_arn = aws_iam_policy.cluster_autoscaler.arn
  role       = aws_iam_role.cluster_autoscaler.name
}

# Helm release — uses auto-discovery via k8s.io/cluster-autoscaler/* tags on ASGs
resource "helm_release" "cluster_autoscaler" {
  name       = "cluster-autoscaler"
  repository = "https://kubernetes.github.io/autoscaler"
  chart      = "cluster-autoscaler"
  version    = var.cluster_autoscaler_version
  namespace  = "kube-system"
  wait       = true
  timeout    = 300

  set {
    name  = "autoDiscovery.clusterName"
    value = local.cluster_name
  }

  set {
    name  = "awsRegion"
    value = var.aws_region
  }

  set {
    name  = "rbac.serviceAccount.name"
    value = local.ca_service_account
  }

  set {
    name  = "rbac.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.cluster_autoscaler.arn
  }

  # Run on system nodes so CA itself doesn't get evicted during scale-down
  set {
    name  = "nodeSelector.role"
    value = "system"
  }

  set {
    name  = "tolerations[0].key"
    value = "CriticalAddonsOnly"
  }

  set {
    name  = "tolerations[0].operator"
    value = "Exists"
  }

  # Allow scale-down of workload nodes even if they run DaemonSet pods (kube-proxy, promtail)
  set {
    name  = "extraArgs.skip-nodes-with-system-pods"
    value = "false"
  }

  # Balance nodes across ASGs with similar instance types (multi-AZ fairness)
  set {
    name  = "extraArgs.balance-similar-node-groups"
    value = "true"
  }

  # Scale down a node after it has been unneeded for 10 minutes
  set {
    name  = "extraArgs.scale-down-unneeded-time"
    value = "10m"
  }

  # Wait 5 min after a scale-up before considering scale-down (avoids flapping)
  set {
    name  = "extraArgs.scale-down-delay-after-add"
    value = "5m"
  }

  # Resources for the CA pod itself — runs on system node, keep it light
  set {
    name  = "resources.requests.cpu"
    value = "100m"
  }

  set {
    name  = "resources.requests.memory"
    value = "128Mi"
  }

  set {
    name  = "resources.limits.cpu"
    value = "200m"
  }

  set {
    name  = "resources.limits.memory"
    value = "256Mi"
  }

  depends_on = [
    aws_iam_role_policy_attachment.cluster_autoscaler,
    helm_release.metrics_server,
  ]
}
