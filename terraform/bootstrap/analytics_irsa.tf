# S3 bucket for NYC taxi analytics Parquet data
resource "aws_s3_bucket" "analytics" {
  bucket = "llmops-analytics-${local.account_id}"

  tags = {
    Name = "llmops-analytics"
  }
}


resource "aws_s3_bucket_server_side_encryption_configuration" "analytics" {
  bucket = aws_s3_bucket.analytics.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "analytics" {
  bucket                  = aws_s3_bucket.analytics.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# IAM policy — read-only access to the analytics bucket
resource "aws_iam_policy" "analytics_s3_read" {
  name        = "${local.cluster_name}-analytics-s3-read"
  description = "Read-only access to analytics Parquet data in S3"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.analytics.arn,
          "${aws_s3_bucket.analytics.arn}/*",
        ]
      }
    ]
  })

  tags = {
    Name = "${local.cluster_name}-analytics-s3-read"
  }
}

# IRSA trust policy for Open WebUI service account — uses shared helper from irsa.tf
# IAM role for Open WebUI pod (analytics S3 access)
resource "aws_iam_role" "analytics_open_webui" {
  name               = "${local.cluster_name}-analytics-open-webui"
  assume_role_policy = local.create_irsa_trust_policy["analytics_open_webui"]

  tags = {
    Name = "${local.cluster_name}-analytics-open-webui"
  }
}

resource "aws_iam_role_policy_attachment" "analytics_open_webui" {
  policy_arn = aws_iam_policy.analytics_s3_read.arn
  role       = aws_iam_role.analytics_open_webui.name
}
