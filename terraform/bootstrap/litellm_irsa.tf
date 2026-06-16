resource "aws_iam_role" "litellm" {
  name               = "${local.cluster_name}-litellm"
  assume_role_policy = local.create_irsa_trust_policy["litellm"]

  tags = {
    Name = "${local.cluster_name}-litellm"
  }
}

resource "aws_iam_policy" "litellm_bedrock" {
  name        = "${local.cluster_name}-litellm-bedrock"
  description = "Allow LiteLLM pod to invoke Bedrock Nova Lite"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = "arn:aws:bedrock:ap-southeast-1::foundation-model/amazon.nova-lite-v1:0"
    }]
  })

  tags = {
    Name = "${local.cluster_name}-litellm-bedrock"
  }
}

resource "aws_iam_role_policy_attachment" "litellm_bedrock" {
  policy_arn = aws_iam_policy.litellm_bedrock.arn
  role       = aws_iam_role.litellm.name
}
