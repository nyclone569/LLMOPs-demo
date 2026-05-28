provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
      Layer       = "eks"
    }
  }
}

# Get VPC data from remote state
data "terraform_remote_state" "vpc" {
  backend = "s3"
  config = {
    bucket = "llmops-tfstate-492"
    key    = "vpc/terraform.tfstate"
    region = var.aws_region
  }
}

# Get current AWS account and caller identity
data "aws_caller_identity" "current" {}

# EKS Cluster
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = var.cluster_name
  cluster_version = var.kubernetes_version

  # Network configuration
  vpc_id                   = data.terraform_remote_state.vpc.outputs.vpc_id
  subnet_ids               = data.terraform_remote_state.vpc.outputs.private_subnet_ids
  control_plane_subnet_ids = data.terraform_remote_state.vpc.outputs.private_subnet_ids

  # Cluster endpoint access
  cluster_endpoint_private_access = var.cluster_endpoint_private_access
  cluster_endpoint_public_access  = var.cluster_endpoint_public_access
  cluster_endpoint_public_access_cidrs = var.cluster_endpoint_public_access_cidrs

  # Enable IRSA (IAM Roles for Service Accounts)
  enable_irsa = true

  # Cluster add-ons — all explicitly configured for scheduling/tolerations
  cluster_addons = {
    coredns = {
      most_recent              = true
      resolve_conflicts_on_create = "OVERWRITE"
      resolve_conflicts_on_update = "OVERWRITE"
      preserve                 = true

      configuration_values = jsonencode({
        replicaCount = 2

        nodeSelector = {
          role = "system"
        }

        tolerations = [
          {
            key      = "CriticalAddonsOnly"
            operator = "Equal"
            value    = "true"
            effect   = "NoSchedule"
          }
        ]
      })
    }
    kube-proxy = {
      most_recent = true
    }
    vpc-cni = {
      most_recent = true
    }
    eks-pod-identity-agent = {
      most_recent = true
    }
  }

  # Cluster logging
  cluster_enabled_log_types = var.cluster_enabled_log_types

  # Access entries (replaces aws-auth ConfigMap in v20+)
  enable_cluster_creator_admin_permissions = true

  # Node security group
  node_security_group_additional_rules = {
    ingress_self_all = {
      description = "Node to node all ports/protocols"
      protocol    = "-1"
      from_port   = 0
      to_port     = 0
      type        = "ingress"
      self        = true
    }
    egress_all = {
      description      = "Node all egress"
      protocol         = "-1"
      from_port        = 0
      to_port          = 0
      type             = "egress"
      cidr_blocks      = ["0.0.0.0/0"]
      ipv6_cidr_blocks = ["::/0"]
    }
  }

  tags = {
    Name = var.cluster_name
  }
}
