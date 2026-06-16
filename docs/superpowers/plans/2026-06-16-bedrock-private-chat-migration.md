# Bedrock Private-Chat Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Ollama with Amazon Bedrock Nova Lite as the `private-chat` backend in LiteLLM, update the analytics pipe to call LiteLLM instead of Ollama directly, and decommission Ollama from the cluster.

**Architecture:** The analytics pipe (`openwebui/filter_analytics.py`) currently bypasses LiteLLM and calls Ollama directly. After this migration it calls LiteLLM's OpenAI-compatible endpoint with model alias `private-chat`, which LiteLLM routes to `bedrock/amazon.nova-lite-v1:0`. LiteLLM gets a new IRSA role with `bedrock:InvokeModel` permissions; Ollama and its 50Gi PVC are removed.

**Tech Stack:** Python (httpx), Terraform (AWS IRSA), Helm/ArgoCD (LiteLLM chart, Open WebUI chart), AWS Bedrock (Nova Lite), Kubernetes

---

## Pre-Flight: Manual AWS Console Step

**Do this before any code changes.**

- [ ] Log into AWS Console → ap-southeast-1 region
- [ ] Go to **Amazon Bedrock → Model access**
- [ ] Find `Amazon Nova Lite` → click **Enable**
- [ ] Wait for status to show **Access granted**

This cannot be automated. Without it every Bedrock call will return a 403.

---

## Task 1: Terraform — LiteLLM IRSA Role

**Files:**
- Modify: `terraform/bootstrap/irsa.tf`
- Create: `terraform/bootstrap/litellm_irsa.tf`
- Modify: `terraform/bootstrap/outputs.tf`

LiteLLM currently runs under the node IAM role. This task creates a dedicated pod-level IRSA role scoped to Bedrock Nova Lite only.

- [ ] **Step 1: Add `litellm` to the IRSA trust policy map in `irsa.tf`**

Open `terraform/bootstrap/irsa.tf`. The `create_irsa_trust_policy` locals block currently has three entries (`aws_load_balancer_controller`, `external_secrets`, `analytics_open_webui`). Add a fourth:

```hcl
      litellm = {
        namespace       = "litellm"
        service_account = "litellm"
      }
```

The full block after the change:

```hcl
locals {
  create_irsa_trust_policy = {
    for key, config in {
      aws_load_balancer_controller = {
        namespace      = "kube-system"
        service_account = "aws-load-balancer-controller"
      }
      external_secrets = {
        namespace      = "external-secrets"
        service_account = "external-secrets"
      }
      analytics_open_webui = {
        namespace       = "open-webui"
        service_account = "open-webui"
      }
      litellm = {
        namespace       = "litellm"
        service_account = "litellm"
      }
    } : key => jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect = "Allow"
        Principal = {
          Federated = local.oidc_provider_arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${local.oidc_provider}:sub" = "system:serviceaccount:${config.namespace}:${config.service_account}"
            "${local.oidc_provider}:aud" = "sts.amazonaws.com"
          }
        }
      }]
    })
  }
}
```

- [ ] **Step 2: Create `terraform/bootstrap/litellm_irsa.tf`**

```hcl
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
```

- [ ] **Step 3: Add output to `terraform/bootstrap/outputs.tf`**

Append to the existing outputs file:

```hcl
output "litellm_role_arn" {
  description = "IRSA role ARN for LiteLLM pod (Bedrock access)"
  value       = aws_iam_role.litellm.arn
}
```

- [ ] **Step 4: Validate and apply**

```bash
cd terraform/bootstrap
terraform init
terraform validate
terraform plan -out=tfplan
# Review: expect 3 new resources — aws_iam_role.litellm, aws_iam_policy.litellm_bedrock, aws_iam_role_policy_attachment.litellm_bedrock
terraform apply tfplan
```

- [ ] **Step 5: Note the role ARN output**

```bash
terraform output litellm_role_arn
# Expected: arn:aws:iam::492372116094:role/llmops-cluster-litellm
```

Copy this ARN — you need it in Task 2.

- [ ] **Step 6: Commit**

```bash
cd /media/sirfenrir/Study/LLMOPs
git add terraform/bootstrap/irsa.tf terraform/bootstrap/litellm_irsa.tf terraform/bootstrap/outputs.tf
git commit -m "feat: add IRSA role for LiteLLM with Bedrock Nova Lite permissions"
```

---

## Task 2: LiteLLM Helm Values — Swap `private-chat` to Bedrock

**Files:**
- Modify: `argocd/helm-values/litellm-values.yaml`

- [ ] **Step 1: Replace the `private-chat` model_list entry**

Find this block in `litellm-values.yaml` (around line 164):

```yaml
    # Private Chat - Local models via Ollama in K8s (sensitive data)
    - model_name: private-chat
      litellm_params:
        model: ollama/llama3.2
        api_base: http://ollama.ollama.svc.cluster.local:11434
        timeout: 300
      model_info:
        mode: chat
```

Replace with:

```yaml
    # Private Chat - Bedrock Nova Lite (replaces Ollama; low-cost, no in-cluster GPU needed)
    - model_name: private-chat
      litellm_params:
        model: bedrock/amazon.nova-lite-v1:0
        aws_region_name: ap-southeast-1
      model_info:
        mode: chat
```

- [ ] **Step 2: Remove the `ollama/llama3.2` entry from `fast-chat`**

Find the third entry in the `fast-chat` load-balanced group (around line 139):

```yaml
    - model_name: fast-chat
      litellm_params:
        model: ollama/llama3.2
        api_base: http://ollama.ollama.svc.cluster.local:11434
        timeout: 300
      model_info:
        mode: chat
```

Delete this entire block. The `fast-chat` alias will remain load-balanced between `gpt-4o-mini` and `gemini-2.0-flash` only.

- [ ] **Step 3: Remove the standalone `llama3.2` model entry**

Find and delete this block (around line 237):

```yaml
    - model_name: llama3.2
      litellm_params:
        model: ollama/llama3.2
        api_base: http://ollama.ollama.svc.cluster.local:11434
      model_info:
        mode: chat
```

- [ ] **Step 4: Add IRSA annotation to LiteLLM ServiceAccount**

Add the following top-level key to `litellm-values.yaml` (after `podAnnotations` at the bottom is fine):

```yaml
# ============================================================================
# SERVICE ACCOUNT — IRSA for Bedrock access
# ============================================================================
serviceAccount:
  create: true
  annotations:
    eks.amazonaws.com/role-arn: arn:aws:iam::492372116094:role/llmops-cluster-litellm
```

Replace `492372116094` with your actual account ID if different (check `terraform output litellm_role_arn` from Task 1).

- [ ] **Step 5: Verify no remaining `ollama` references in model_list**

```bash
grep -n "ollama" argocd/helm-values/litellm-values.yaml
```

Expected: zero matches (the `model_settings.private-chat` rate limit entry has no ollama reference, so it stays).

- [ ] **Step 6: Commit**

```bash
git add argocd/helm-values/litellm-values.yaml
git commit -m "feat: swap private-chat to Bedrock Nova Lite, remove Ollama model entries"
```

---

## Task 3: Update `filter_analytics.py` — Point Pipe at LiteLLM

**Files:**
- Modify: `openwebui/filter_analytics.py`

The pipe currently calls Ollama directly on lines 161-191 and has `ollama_url`/`ollama_model` parameters throughout. This task replaces all of that with LiteLLM equivalents and adds the auth header.

- [ ] **Step 1: Replace the three constants (lines 161-163)**

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

- [ ] **Step 2: Replace `_ollama_chat` with `_llm_chat` (lines 166-174)**

```python
def _llm_chat(messages: list[dict], model: str = LITELLM_MODEL, litellm_url: str = LITELLM_URL, api_key: str = "") -> str:
    """HTTP call to LiteLLM OpenAI-compatible endpoint."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    resp = httpx.post(
        litellm_url,
        json={"model": model, "messages": messages},
        headers=headers,
        timeout=LITELLM_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
```

- [ ] **Step 3: Replace `_stream_ollama` with `_stream_llm` (lines 177-190)**

```python
async def _stream_llm(messages: list[dict], litellm_url: str = LITELLM_URL, model: str = LITELLM_MODEL, api_key: str = "") -> StreamingResponse:
    """Stream LiteLLM response as SSE bytes."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def generator():
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                litellm_url,
                json={"model": model, "messages": messages, "stream": True},
                headers=headers,
                timeout=LITELLM_TIMEOUT,
            ) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream")
```

- [ ] **Step 4: Update `_run_supervisor` signature and call (lines 225-241)**

Change the function signature from:
```python
def _run_supervisor(question: str, registry: dict, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> dict:
```
To:
```python
def _run_supervisor(question: str, registry: dict, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> dict:
```

Change the internal call from:
```python
    raw = _ollama_chat(messages, model=ollama_model, ollama_url=ollama_url)
```
To:
```python
    raw = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
```

- [ ] **Step 5: Update `_run_query` signature and call (lines 260-307)**

Change the function signature from:
```python
def _run_query(question: str, table: str, registry: dict, s3_bucket: str, aws_region: str = AWS_REGION, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> dict:
```
To:
```python
def _run_query(question: str, table: str, registry: dict, s3_bucket: str, aws_region: str = AWS_REGION, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> dict:
```

Change the internal call from:
```python
    raw = _ollama_chat(messages, model=ollama_model, ollama_url=ollama_url)
```
To:
```python
    raw = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
```

- [ ] **Step 6: Update `_run_summarize` signature and call (lines 333-350)**

Change the function signature from:
```python
def _run_summarize(question: str, rows: list[dict], capped: bool, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> dict:
```
To:
```python
def _run_summarize(question: str, rows: list[dict], capped: bool, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> dict:
```

Change the internal call from:
```python
    raw = _ollama_chat(messages, model=ollama_model, ollama_url=ollama_url)
```
To:
```python
    raw = _llm_chat(messages, model=litellm_model, litellm_url=litellm_url, api_key=api_key)
```

- [ ] **Step 7: Update `Valves` and `pipe` method (lines 353-430)**

Replace the `Valves` inner class:

```python
class Valves(BaseModel):
    """Open WebUI admin-configurable settings for this pipe."""
    s3_bucket: str = S3_BUCKET
    aws_region: str = AWS_REGION
    litellm_url: str = LITELLM_URL
    litellm_model: str = LITELLM_MODEL
    litellm_api_key: str = ""
    enabled: bool = True
```

In the `pipe` method, replace every `_stream_ollama(` call with `_stream_llm(` and every `self.valves.ollama_url` / `self.valves.ollama_model` with `self.valves.litellm_url` / `self.valves.litellm_model`. Also pass `api_key=self.valves.litellm_api_key` to each call.

The four `_stream_ollama` call sites become:
```python
return await _stream_llm(
    body.get("messages", []),
    self.valves.litellm_url,
    self.valves.litellm_model,
    self.valves.litellm_api_key,
)
```

- [ ] **Step 8: Update `_run_analytics` signature and calls (lines 444-463)**

Change the function signature from:
```python
def _run_analytics(question: str, s3_bucket: str, aws_region: str = AWS_REGION, ollama_url: str = OLLAMA_URL, ollama_model: str = OLLAMA_MODEL) -> str:
```
To:
```python
def _run_analytics(question: str, s3_bucket: str, aws_region: str = AWS_REGION, litellm_url: str = LITELLM_URL, litellm_model: str = LITELLM_MODEL, api_key: str = "") -> str:
```

Update the three internal calls:
```python
supervisor = _run_supervisor(question, REGISTRY, litellm_url, litellm_model, api_key)
# ...
query_result = _run_query(question, table, REGISTRY, s3_bucket, aws_region, litellm_url, litellm_model, api_key)
# ...
summarize_result = _run_summarize(question, rows, capped, litellm_url, litellm_model, api_key)
```

Update the call site in `pipe` (the `_run_analytics(...)` call) to pass:
```python
result = _run_analytics(
    question,
    self.valves.s3_bucket,
    self.valves.aws_region,
    self.valves.litellm_url,
    self.valves.litellm_model,
    self.valves.litellm_api_key,
)
```

- [ ] **Step 9: Verify no remaining `ollama` references**

```bash
grep -in "ollama" openwebui/filter_analytics.py
```

Expected: zero matches.

- [ ] **Step 10: Run the unit tests for the LIMIT injection logic**

```bash
python3 -c "
import re
ROW_CAP = 200

def inject_limit(sql):
    depth, top_limit = 0, False
    for tok in re.split(r'(\(|\))', sql):
        if tok == '(':
            depth += 1
        elif tok == ')':
            depth -= 1
        elif depth == 0 and re.search(r'\bLIMIT\s+\d+', tok, re.IGNORECASE):
            top_limit = True
            break
    if not top_limit:
        return f'SELECT * FROM ({sql}) _q LIMIT {ROW_CAP + 1}'
    return sql

cases = [
    ('SELECT zone FROM kpi_zone_performance ORDER BY revenue DESC', True),
    ('SELECT zone FROM kpi_zone_performance LIMIT 10', False),
    ('WITH x AS (SELECT zone FROM kpi_zone_performance LIMIT 500) SELECT * FROM x', True),
    ('select * from kpi_daily_overview limit 50', False),
]
for sql, expect_wrap in cases:
    result = inject_limit(sql)
    wrapped = '_q LIMIT' in result
    status = 'PASS' if wrapped == expect_wrap else 'FAIL'
    print(f'{status}: {sql[:60]}...')
"
```

Expected: all four lines print `PASS`.

- [ ] **Step 11: Commit**

```bash
git add openwebui/filter_analytics.py
git commit -m "feat: migrate analytics pipe from Ollama to LiteLLM/Bedrock"
```

---

## Task 4: Decommission Ollama

**Files:**
- Delete: `argocd/apps/ollama.yaml`

- [ ] **Step 1: Confirm LiteLLM is routing to Bedrock successfully first**

Before deleting Ollama, verify LiteLLM synced with the new config:

```bash
kubectl rollout status deployment -n litellm
kubectl logs -n litellm -l app.kubernetes.io/name=litellm --tail=20
```

Expected: no error lines containing `ollama` or `connection refused`.

- [ ] **Step 2: Delete the ArgoCD app file**

```bash
rm argocd/apps/ollama.yaml
```

- [ ] **Step 3: Commit and push**

```bash
git add -u argocd/apps/ollama.yaml
git commit -m "feat: decommission Ollama — replaced by Bedrock Nova Lite"
git push
```

ArgoCD will detect the missing app and (with `prune: true`) remove the Ollama Helm release and namespace on next sync (~2 minutes).

- [ ] **Step 4: Confirm Ollama is gone**

```bash
kubectl get pods -n ollama
```

Expected: `No resources found in ollama namespace.` or namespace not found.

- [ ] **Step 5: Note PVC for later deletion**

```bash
kubectl get pvc -n ollama 2>/dev/null || echo "namespace already gone"
```

**Do NOT delete the PVC yet.** Wait at least 24 hours (or until you're confident rollback is not needed). Then:

```bash
# Only run this after rollback window has passed
kubectl delete pvc -n ollama --all
```

---

## Task 5: Wire Up API Key in Open WebUI

This is a runtime configuration step — not code. Do it after ArgoCD syncs Tasks 2-4.

- [ ] **Step 1: Get the LiteLLM master key value**

```bash
kubectl get secret -n open-webui llmops-apikeys-secret -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d
```

Copy the output.

- [ ] **Step 2: Set the valve in Open WebUI**

1. Open Open WebUI in browser
2. Go to **Admin Panel → Functions → NYC Taxi Analytics Pipe → Settings (gear icon)**
3. Set `litellm_api_key` to the value from Step 1
4. Click Save

- [ ] **Step 3: Test the analytics pipeline end-to-end**

Send this message in Open WebUI:

> "show monthly revenue trend"

Expected flow:
- Status indicator shows "Analyzing"
- Response returns a 2-4 sentence summary + bar/line chart
- No error message

If it fails, check LiteLLM logs:
```bash
kubectl logs -n litellm -l app.kubernetes.io/name=litellm --tail=50 | grep -E "error|bedrock|private-chat"
```

---

## Self-Review Checklist

Spec coverage:

| Spec requirement | Task |
|---|---|
| Enable Bedrock Model Access (console) | Pre-Flight |
| Create LiteLLM IRSA role + Bedrock policy | Task 1 |
| Add `litellm` to IRSA trust map | Task 1 step 1 |
| Output role ARN from Terraform | Task 1 step 3 |
| Swap `private-chat` to `bedrock/amazon.nova-lite-v1:0` | Task 2 step 1 |
| Remove `ollama/llama3.2` from `fast-chat` | Task 2 step 2 |
| Remove standalone `llama3.2` model entry | Task 2 step 3 |
| Add `serviceAccount.annotations` with IRSA ARN | Task 2 step 4 |
| Replace OLLAMA_* constants with LITELLM_* | Task 3 step 1 |
| Rename `_ollama_chat` → `_llm_chat` with auth header | Task 3 step 2 |
| Rename `_stream_ollama` → `_stream_llm` with auth header | Task 3 step 3 |
| Update all function signatures (ollama_url → litellm_url) | Task 3 steps 4-8 |
| Add `litellm_api_key` to Valves | Task 3 step 7 |
| Delete `argocd/apps/ollama.yaml` | Task 4 step 2 |
| PVC deletion deferred until rollback window passes | Task 4 step 5 |
| Set `litellm_api_key` valve in Open WebUI UI | Task 5 step 2 |
| End-to-end analytics test | Task 5 step 3 |
