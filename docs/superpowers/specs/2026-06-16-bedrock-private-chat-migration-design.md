# Bedrock Private-Chat Migration Design

**Date:** 2026-06-16
**Status:** Approved
**Author:** nghiatd

## Problem

Ollama runs `llama3.2` on CPU on the same `t3.large` node as Open WebUI. During analytics
queries the pipe fires three sequential Ollama calls (supervisor → query → summarize). Each
call spikes CPU to ~1500m on a 2-vCPU node, starving Open WebUI of CPU and causing repeated
liveness probe failures (exit 137). The node cannot run co-located CPU inference at this size.

## Goal

Replace Ollama with Amazon Bedrock for the `private-chat` model alias. Remove Ollama from
the cluster entirely. The analytics pipe continues to call the same three agents; only the
backend changes.

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
  filter_analytics.py → LiteLLM proxy → Bedrock (amazon.nova-lite-v1:0, ap-southeast-1)
  _stream_llm()       → LiteLLM proxy → Bedrock
```

LiteLLM already exposes an OpenAI-compatible endpoint at
`http://litellm.litellm.svc.cluster.local:4000/v1`. The pipe switches its base URL and
model constant to target this endpoint with model `private-chat`.

Langfuse tracing, Redis caching, and LiteLLM cost tracking apply automatically to all
Bedrock calls because they flow through the existing LiteLLM pipeline.

## Changes

### 1. `litellm-values.yaml` — swap `private-chat` backend

Remove both `ollama/llama3.2` entries under `private-chat` and replace with Bedrock:

```yaml
- model_name: private-chat
  litellm_params:
    model: bedrock/amazon.nova-lite-v1:0
    aws_region_name: ap-southeast-1
  model_info:
    mode: chat
```

Also remove the `ollama/llama3.2` fallback entry from `fast-chat` (the third entry in that
load-balanced group) — Ollama will no longer exist to receive fallback traffic.

Remove the standalone `llama3.2` model entry entirely.

Update `router_settings.fallbacks`: replace `private-chat` references in fallback chains
with a valid surviving model (e.g. `claude-sonnet`) or remove them.

### 2. `filter_analytics.py` — point at LiteLLM

Replace Ollama-specific constants and function names:

```python
# Before
OLLAMA_URL = "http://ollama.ollama.svc.cluster.local:11434/v1/chat/completions"
OLLAMA_MODEL = "qwen2.5-coder:7b"
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

Add the LiteLLM master key header to authenticated calls:
```python
headers={"Authorization": f"Bearer {self.valves.litellm_api_key}"}
```
Add `litellm_api_key: str = ""` to `Valves` — populated via Open WebUI admin UI from the
existing `LITELLM_MASTER_KEY` secret value.

### 3. `terraform/bootstrap/litellm_irsa.tf` — new file

Create an IRSA role for the LiteLLM service account with Bedrock invoke permissions:

```hcl
resource "aws_iam_role" "litellm" {
  name               = "${local.cluster_name}-litellm"
  assume_role_policy = # trust policy for litellm/litellm service account
}

resource "aws_iam_policy" "litellm_bedrock" {
  name = "${local.cluster_name}-litellm-bedrock"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = "arn:aws:bedrock:ap-southeast-1::foundation-model/amazon.nova-lite-v1:0"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "litellm_bedrock" {
  policy_arn = aws_iam_policy.litellm_bedrock.arn
  role       = aws_iam_role.litellm.name
}
```

Add `litellm` to the `create_irsa_trust_policy` map in `irsa.tf`:
```hcl
litellm = {
  namespace       = "litellm"
  service_account = "litellm"
}
```

Add a Kubernetes ServiceAccount annotation in the LiteLLM Helm chart values or a separate
manifest to bind the role ARN.

### 4. `argocd/apps/ollama.yaml` — delete

ArgoCD `prune: true` will remove the Ollama Helm release and its namespace on next sync.
The PVC (`ollama-0`, 50Gi gp3) must be manually deleted after confirming Ollama is gone —
ArgoCD does not delete PVCs automatically.

## IAM: Bedrock Model Access

Nova Lite must be enabled in the AWS console before deployment:
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
4. The IRSA role and IAM policy are additive and do not need to be removed for rollback.

## Open Questions

None — all decisions resolved during design.
