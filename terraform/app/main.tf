provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
      Layer       = "app"
    }
  }
}

# Get VPC data from remote state
data "terraform_remote_state" "eks" {
  backend = "s3"
  config = {
    bucket = "llmops-tfstate-492"
    key    = "eks/terraform.tfstate"
    region = var.aws_region
  }
}

data "terraform_remote_state" "vpc" {
  backend = "s3"
  config = {
    bucket = "llmops-tfstate-492"
    key    = "vpc/terraform.tfstate"
    region = var.aws_region
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  cluster_name           = data.terraform_remote_state.eks.outputs.cluster_name
  vpc_id                 = data.terraform_remote_state.vpc.outputs.vpc_id
  private_subnet_ids     = data.terraform_remote_state.vpc.outputs.private_subnet_ids
  node_security_group_id = data.terraform_remote_state.eks.outputs.node_security_group_id
  account_id             = data.aws_caller_identity.current.account_id
}
