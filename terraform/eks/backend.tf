terraform {
  backend "s3" {
    bucket         = "llmops-tfstate-492"
    key            = "eks/terraform.tfstate"
    region         = "ap-southeast-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}
