# Bedrock Private-Chat Migration Design

**Date:** 2026-06-16
**Status:** Draft
**Author:** nghiatd

## Problem

Ollama runs `llama3.2` on CPU on the same `t3.large` node as Open WebUI. During analytics
queries the pipe fires three sequential Ollama calls (supervisor → query → summarize). Each
call spikes CPU to ~1500m on a 2-vCPU node, starving Open WebUI of CPU and causing repeated
liveness probe failures (exit 137). The node cannot run co-located CPU inference at this size.

Note: the analytics pipe also had a latent bug — `OLLAMA_MODEL = "qwen2.5-coder:7b"` but
Ollama was only configured to pull `llama3.2`. All three agent calls were failing with
model-not-found. This migration fixes both the CPU starvation and the model mismatch.

## Goal

Replace Ollama with Amazon Bedrock (Nova Lite) for the `private-chat` model alias in
LiteLLM. Update the analytics pipe to call LiteLLM instead of Ollama directly. Remove
Ollama from the cluster entirely. The analytics pipeline logic and agent prompts are
unchanged; only the inference backend changes.

## Non-Goals

- Changing the analytics pipeline logic or agent prompts
- Migrating `fast-chat`, `coding-assistant`, or any other LiteLLM alias
- Adding streaming to the analytics pipeline (supervisor/query/summarize are non-streaming)

## Architecture

```
Before:
  filter_analytics.py → Ollama pod (ollama namespace, t3.large node)
  _stream_ollama()    → Ollama pod

After:
  filter_analytics.py → LiteLLM proxy → Bedrock (apac.amazon.nova-lite-v1:0 inference profile, ap-southeast-1)
  _stream_llm()       → LiteLLM proxy → Bedrock
```

LiteLLM already exposes an OpenAI-compatible endpoint at
`http://litellm.litellm.svc.cluster.local:4000/v1`. The pipe switches its base URL and
model constant to target this endpoint with model `private-chat`.

Langfuse tracing, Redis caching, and LiteLLM cost tracking apply automatically to all
Bedrock calls because they flow through the existing LiteLLM pipeline.

`private-chat` remains in the fallback chains (e.g. `claude-sonnet: [private-chat]`).
After migration those fallbacks route to Bedrock Nova Lite — paid but cheap (~$0.00006/1K
input tokens) and reliable. No fallback chain cleanup is required.

## Changes

### 1. `litellm-values.yaml` — swap `private-chat` backend

Remove the single `ollama/llama3.2` entry under `private-chat` in `model_list` and replace
with Bedrock:

```yaml
- model_name: private-chat
  litellm_params:
    model: bedrock/apac.amazon.nova-lite-v1:0
    aws_region_name: ap-southeast-1
  model_info:
    mode: chat
```

Also remove:
- The `ollama/llama3.2` entry from the `fast-chat` load-balanced group (third entry)
- The standalone `llama3.2` model entry

The `private-chat` entry in `model_settings` (rate limit config, not `model_list`) stays
unchanged — it applies to the alias regardless of backend.

Add the IRSA role ARN to the LiteLLM ServiceAccount via `serviceAccount.annotations`:

```yaml
serviceAccount:
  create: true
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::492372116094:role/llmops-cluster-litellm
```

Do not use a separate manifest — the chart manages its own ServiceAccount and a separate
manifest would conflict.

### 2. `filter_analytics.py` — point at LiteLLM

Replace Ollama-specific constants and function names. Note: `qwen2.5-coder:7b` was the old
(incorrect) model name — Ollama only had `llama3.2`. The rename below corrects both.

```python
# Before
OLLAMA_URL = "http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions"
OLLAMA_MODEL = "qwen2.5-coder:7b"   # was wrong — Ollama only had llama3.2
OLLAMA_TIMEOUT = 60

# After
LITELLM_URL = "http://litellm.litellm.svc.cluster.local:4000/v1/chat/completions"
LITELLM_MODEL = "private-chat"
LITELLM_TIMEOUT = 60
```

Rename functions and parameters:
- `_ollama_chat` → `_llm_chat`
- `_stream_ollama` → `_stream_llm`
- All `ollama_url` / `ollama_model` parameter names → `litellm_url` / `litellm_model`
- `Valves.ollama_url` / `Valves.ollama_model` → `Valves.litellm_url` / `Valves.litellm_model`

Add LiteLLM master key auth to all calls (LiteLLM requires authentication):
```python
headers={"Authorization": f"Bearer {self.valves.litellm_api_key}"}
```

Add `litellm_api_key: str = ""` to `Valves`. Set this via Open WebUI admin UI → Pipe
settings → `litellm_api_key` using the value of the `LITELLM_MASTER_KEY` secret. The
master key is appropriate here (not a scoped virtual key) because the pipe bypasses team
model restrictions by design and needs access to `private-chat` unconditionally.

### 3. `terraform/bootstrap/litellm_irsa.tf` — new file

LiteLLM pods currently run on the node IAM role (no pod-level IRSA). Create a dedicated
IRSA role following the same pattern as `analytics_irsa.tf`:

Add `litellm` to the `create_irsa_trust_policy` map in `irsa.tf`:
```hcl
litellm = {
  namespace       = "litellm"
  service_account = "litellm"
}
```

New file `litellm_irsa.tf`:
```hcl
resource "aws_iam_role" "litellm" {
  name               = "${local.cluster_name}-litellm"
  assume_role_policy = local.create_irsa_trust_policy["litellm"]

  tags = {
    Name = "${local.cluster_name}-litellm"
  }
}

resource "aws_iam_policy" "litellm_bedrock" {
  name = "${local.cluster_name}-litellm-bedrock"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = [
        "arn:aws:bedrock:ap-southeast-1::foundation-model/amazon.nova-lite-v1:0",
        "arn:aws:bedrock:ap-southeast-1:492372116094:inference-profile/apac.amazon.nova-lite-v1:0",
      ]
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
```

Output the role ARN so it can be pasted into `litellm-values.yaml`:
```hcl
output "litellm_role_arn" {
  value = aws_iam_role.litellm.arn
}
```

### 4. `argocd/apps/ollama.yaml` — delete

ArgoCD `prune: true` will remove the Ollama Helm release and its namespace on next sync.

**The 50Gi PVC (`ollama-0`, gp3) must be manually deleted after confirming Ollama pods are
gone.** Do not delete it during the same deployment window — wait until confident the
rollback window has passed. ArgoCD does not delete PVCs automatically.

## IAM: Bedrock Model Access

Nova Lite must be enabled in the AWS console **before** deploying:
**Bedrock → Model access → amazon.nova-lite-v1 → Enable** (ap-southeast-1 region).

This is a one-time manual step; it cannot be automated via Terraform.

## Sequence Diagram

```
User query (analytics intent)
  → Open WebUI pipe
    → LiteLLM :4000 (private-chat / supervisor call)
      → Bedrock nova-lite → JSON response
    → DuckDB → S3 Parquet
    → LiteLLM :4000 (private-chat / query call)
      → Bedrock nova-lite → SQL
    → LiteLLM :4000 (private-chat / summarize call)
      → Bedrock nova-lite → summary + chart_spec
  → response rendered in Open WebUI
```

## Rollback

1. Restore `argocd/apps/ollama.yaml` and push — ArgoCD re-installs Ollama.
2. Revert `filter_analytics.py` constants to Ollama URL/model.
3. Revert `litellm-values.yaml` `private-chat` entry.
4. The IRSA role and IAM policy are additive — no removal needed for rollback.
5. **Do not delete the PVC until the rollback window has passed.** If the PVC is gone,
   Ollama cannot restart and rollback requires a full model re-download (50Gi).
