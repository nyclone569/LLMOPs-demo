resource "aws_iam_role" "ebs_csi_driver" {
    name = "${local.cluster_name}-ebs-csi-driver"

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
            "${local.oidc_provider}:sub" = "system:serviceaccount:kube-system:ebs-csi-controller-sa"
            "${local.oidc_provider}:aud" = "sts.amazonaws.com"
          }
        }
      }]
    })

    tags = {
      Name = "${local.cluster_name}-ebs-csi-driver"
    }
  }

  resource "aws_iam_role_policy_attachment" "ebs_csi_driver" {
    policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
    role       = aws_iam_role.ebs_csi_driver.name
  }

  resource "aws_eks_addon" "ebs_csi_driver" {
    cluster_name             = local.cluster_name
    addon_name               = "aws-ebs-csi-driver"
    service_account_role_arn = aws_iam_role.ebs_csi_driver.arn

    resolve_conflicts_on_create = "OVERWRITE"
    resolve_conflicts_on_update = "OVERWRITE"

    depends_on = [
      aws_iam_role_policy_attachment.ebs_csi_driver
    ]

    tags = {
      Name = "${local.cluster_name}-ebs-csi-driver"
    }
  }
