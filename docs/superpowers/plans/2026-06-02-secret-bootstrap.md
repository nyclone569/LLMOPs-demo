# Secret Bootstrap Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a single `.env.secrets` file and a `push-secrets.sh` script so any developer can repopulate all AWS Secrets Manager values with one command after a `terraform apply`.

**Architecture:** `secrets/.env.example` (committed, documents all keys) is copied to `secrets/.env.secrets` (gitignored, holds real values). `scripts/push-secrets.sh` sources that file, validates all 14 required keys, then upserts two AWS Secrets Manager paths (`llmops/apikeys`, `llmops/supabase`) as JSON via the AWS CLI. Python is used inline for JSON serialisation to safely handle special characters in passwords.

**Tech Stack:** Bash, Python 3 (stdlib `json` + `os`), AWS CLI v2

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `secrets/.env.example` | Committed template — all 17 keys documented, no real values |
| Create | `secrets/.env.secrets` | Gitignored — developer fills in real values here |
| Create | `scripts/push-secrets.sh` | Validates + pushes secrets to AWS SM |
| Modify | `.gitignore` | Exclude `secrets/.env.secrets` from git |

---

## Task 1: Update `.gitignore`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Add the secrets file pattern**

Open `.gitignore` and append at the end of the `# Secrets` block (after the `.env.local` line):

```
# Secrets
.env
.env.local
secrets/.env.secrets
*.tfvars
!*.tfvars.example
```

- [ ] **Step 2: Verify it won't track the file**

```bash
git check-ignore -v secrets/.env.secrets
```

Expected output:
```
.gitignore:3:secrets/.env.secrets	secrets/.env.secrets
```

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore secrets/.env.secrets"
```

---

## Task 2: Create `secrets/.env.example`

**Files:**
- Create: `secrets/.env.example`

- [ ] **Step 1: Create the `secrets/` directory and write the file**

Create `secrets/.env.example` with this exact content:

```bash
# =============================================================================
# LLMOps Platform — Secret Keys Template
# =============================================================================
# HOW TO USE:
#   cp secrets/.env.example secrets/.env.secrets
#   # Fill in all Required (✅) values below
#   ./scripts/push-secrets.sh
#
# secrets/.env.secrets is gitignored — never commit it.
# =============================================================================

# -----------------------------------------------------------------------------
# AWS SM path: llmops/apikeys  →  k8s secret: llmops-apikeys-secret
# Namespaces:  langfuse, litellm, open-webui, postgresql, redis
# -----------------------------------------------------------------------------

# ✅ Required — PostgreSQL admin password
# Used by: postgresql chart (admin), langfuse DB auth
POSTGRESQL_PASSWORD=

# ✅ Required — Redis auth password
# Used by: redis chart, langfuse queue, litellm cache
REDIS_PASSWORD=

# ✅ Required — LiteLLM API gateway master key (format: sk-...)
# Used by: litellm (auth), open-webui (as its OpenAI-compatible API key)
LITELLM_MASTER_KEY=

# ✅ Required — LiteLLM encryption salt (random string, e.g. openssl rand -hex 32)
# Used by: litellm (encrypts stored keys in DB)
LITELLM_SALT_KEY=

# ✅ Required — Langfuse app secret / NextAuth secret (random string)
# Used by: langfuse web (salt + NextAuth), litellm Langfuse callback
LANGFUSE_SECRET_KEY=

# ✅ Required — Langfuse public API key (format: pk-lf-...)
# Used by: langfuse web, litellm Langfuse callback
LANGFUSE_PUBLIC_KEY=

# ✅ Required — ClickHouse DB password for Langfuse analytics
# Used by: langfuse-clickhouse container
CLICKHOUSE_PASSWORD=

# ✅ Required — Open WebUI session signing secret (random string)
# Used by: open-webui (signs user sessions)
WEBUI_SECRET_KEY=

# ✅ Required — OpenAI API key
# Used by: litellm (gpt-4o, gpt-4o-mini models)
OPENAI_API_KEY=

# ✅ Required — Anthropic API key
# Used by: litellm (claude-* models)
ANTHROPIC_API_KEY=

# ✅ Required — Google Gemini API key
# Used by: litellm (gemini-2.0-flash, long-context models)
GEMINI_API_KEY=

# ✅ Required — AWS IAM access key ID for Langfuse S3 bucket (llmops-langfuse)
# Used by: langfuse (stores trace blobs in S3)
LANGFUSE_S3_ACCESS_KEY_ID=

# ✅ Required — AWS IAM secret access key for Langfuse S3 bucket
# Used by: langfuse (stores trace blobs in S3)
LANGFUSE_S3_SECRET_ACCESS_KEY=

# ⬜ Optional — OIDC/SSO provider URL (leave blank to disable SSO in Open WebUI)
# Example: https://accounts.google.com or https://your-keycloak/realms/master
OIDC_PROVIDER_URL=

# ⬜ Optional — OIDC client ID (required if OIDC_PROVIDER_URL is set)
OIDC_CLIENT_ID=

# ⬜ Optional — OIDC client secret (required if OIDC_PROVIDER_URL is set)
OIDC_CLIENT_SECRET=

# -----------------------------------------------------------------------------
# AWS SM path: llmops/supabase  →  k8s secret: llmops-supabase-secret
# Namespaces:  langfuse, litellm
# -----------------------------------------------------------------------------

# ✅ Required — PostgreSQL connection string for LiteLLM spend tracking
# Format: postgres://postgres:<POSTGRESQL_PASSWORD>@postgresql-primary.postgresql.svc.cluster.local:5432/postgres
# Replace <POSTGRESQL_PASSWORD> with the value above.
LITELLM_DB_URL=
```

- [ ] **Step 2: Verify the file is committed-safe (no real values)**

```bash
grep -v '^#' secrets/.env.example | grep -v '^$' | grep '=.'
```

Expected: no output (all values after `=` should be empty in .env.example).

- [ ] **Step 3: Commit**

```bash
git add secrets/.env.example
git commit -m "feat: add secrets/.env.example with all required key documentation"
```

---

## Task 3: Create `secrets/.env.secrets`

**Files:**
- Create: `secrets/.env.secrets` (will be gitignored)

- [ ] **Step 1: Copy the example file**

```bash
cp secrets/.env.example secrets/.env.secrets
```

- [ ] **Step 2: Confirm it is ignored by git**

```bash
git status secrets/.env.secrets
```

Expected output:
```
On branch main
nothing to commit, working tree clean
```
(The file should not appear in git status at all.)

- [ ] **Step 3: No commit needed** — this file is gitignored and local only.

---

## Task 4: Create `scripts/push-secrets.sh`

**Files:**
- Create: `scripts/push-secrets.sh`

- [ ] **Step 1: Create the `scripts/` directory and write the script**

Create `scripts/push-secrets.sh` with this exact content:

```bash
#!/usr/bin/env bash
# push-secrets.sh — Populate AWS Secrets Manager from secrets/.env.secrets
# Usage: ./scripts/push-secrets.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/../secrets/.env.secrets"
AWS_REGION="${AWS_DEFAULT_REGION:-ap-southeast-1}"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=true
  echo "DRY RUN — no changes will be made to AWS Secrets Manager"
fi

# ── Load secrets file ──────────────────────────────────────────────────────────
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: secrets/.env.secrets not found."
  echo "  Run: cp secrets/.env.example secrets/.env.secrets"
  echo "  Then fill in all required values and re-run."
  exit 1
fi

# Export all variables from the file (ignores comment lines)
set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

# ── Validate required keys ─────────────────────────────────────────────────────
REQUIRED_KEYS=(
  POSTGRESQL_PASSWORD
  REDIS_PASSWORD
  LITELLM_MASTER_KEY
  LITELLM_SALT_KEY
  LANGFUSE_SECRET_KEY
  LANGFUSE_PUBLIC_KEY
  CLICKHOUSE_PASSWORD
  WEBUI_SECRET_KEY
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  GEMINI_API_KEY
  LANGFUSE_S3_ACCESS_KEY_ID
  LANGFUSE_S3_SECRET_ACCESS_KEY
  LITELLM_DB_URL
)

MISSING=()
for key in "${REQUIRED_KEYS[@]}"; do
  if [[ -z "${!key:-}" ]]; then
    MISSING+=("$key")
  fi
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "ERROR: The following required keys are missing or empty in secrets/.env.secrets:"
  for k in "${MISSING[@]}"; do
    echo "  - $k"
  done
  exit 1
fi

echo "All 14 required keys present."

# ── Build JSON for llmops/apikeys ──────────────────────────────────────────────
APIKEYS_JSON=$(python3 - <<'PYEOF'
import json, os

d = {
    "POSTGRESQL_PASSWORD":        os.environ["POSTGRESQL_PASSWORD"],
    "REDIS_PASSWORD":             os.environ["REDIS_PASSWORD"],
    "LITELLM_MASTER_KEY":         os.environ["LITELLM_MASTER_KEY"],
    "LITELLM_SALT_KEY":           os.environ["LITELLM_SALT_KEY"],
    "LANGFUSE_SECRET_KEY":        os.environ["LANGFUSE_SECRET_KEY"],
    "LANGFUSE_PUBLIC_KEY":        os.environ["LANGFUSE_PUBLIC_KEY"],
    "CLICKHOUSE_PASSWORD":        os.environ["CLICKHOUSE_PASSWORD"],
    "WEBUI_SECRET_KEY":           os.environ["WEBUI_SECRET_KEY"],
    "OPENAI_API_KEY":             os.environ["OPENAI_API_KEY"],
    "ANTHROPIC_API_KEY":          os.environ["ANTHROPIC_API_KEY"],
    "GEMINI_API_KEY":             os.environ["GEMINI_API_KEY"],
    "LANGFUSE_S3_ACCESS_KEY_ID":  os.environ["LANGFUSE_S3_ACCESS_KEY_ID"],
    "LANGFUSE_S3_SECRET_ACCESS_KEY": os.environ["LANGFUSE_S3_SECRET_ACCESS_KEY"],
}

# Include optional OIDC keys only when non-empty
for k in ("OIDC_PROVIDER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"):
    if os.environ.get(k, "").strip():
        d[k] = os.environ[k]

print(json.dumps(d))
PYEOF
)

# ── Build JSON for llmops/supabase ─────────────────────────────────────────────
SUPABASE_JSON=$(python3 - <<'PYEOF'
import json, os
print(json.dumps({"LITELLM_DB_URL": os.environ["LITELLM_DB_URL"]}))
PYEOF
)

# ── Push or dry-run ────────────────────────────────────────────────────────────
if [[ "$DRY_RUN" == "true" ]]; then
  echo ""
  echo "── llmops/apikeys JSON (keys only) ──────────────────────────────────────"
  echo "$APIKEYS_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'  {k}') for k in d]"
  echo ""
  echo "── llmops/supabase JSON (keys only) ─────────────────────────────────────"
  echo "$SUPABASE_JSON" | python3 -c "import json,sys; d=json.load(sys.stdin); [print(f'  {k}') for k in d]"
  echo ""
  echo "Dry run complete. Re-run without --dry-run to push to AWS."
  exit 0
fi

echo "Pushing llmops/apikeys..."
aws secretsmanager put-secret-value \
  --region "$AWS_REGION" \
  --secret-id "llmops/apikeys" \
  --secret-string "$APIKEYS_JSON" \
  --output json | python3 -c "import json,sys; v=json.load(sys.stdin); print(f'  ARN: {v[\"ARN\"]}  VersionId: {v[\"VersionId\"]}')"

echo "Pushing llmops/supabase..."
aws secretsmanager put-secret-value \
  --region "$AWS_REGION" \
  --secret-id "llmops/supabase" \
  --secret-string "$SUPABASE_JSON" \
  --output json | python3 -c "import json,sys; v=json.load(sys.stdin); print(f'  ARN: {v[\"ARN\"]}  VersionId: {v[\"VersionId\"]}')"

echo ""
echo "Done at $(date -u +%Y-%m-%dT%H:%M:%SZ)."
echo "ExternalSecrets will resync within ~60s. Watch with:"
echo "  kubectl get externalsecrets -A -w"
```

- [ ] **Step 2: Make the script executable**

```bash
chmod +x scripts/push-secrets.sh
```

- [ ] **Step 3: Test — missing .env.secrets triggers a clear error**

```bash
mv secrets/.env.secrets secrets/.env.secrets.bak 2>/dev/null || true
./scripts/push-secrets.sh
```

Expected output:
```
ERROR: secrets/.env.secrets not found.
  Run: cp secrets/.env.example secrets/.env.secrets
  Then fill in all required values and re-run.
```
Exit code should be 1:
```bash
echo "exit code: $?"   # → exit code: 1
```

Restore:
```bash
mv secrets/.env.secrets.bak secrets/.env.secrets 2>/dev/null || cp secrets/.env.example secrets/.env.secrets
```

- [ ] **Step 4: Test — empty required key triggers a clear error**

Add a temp override to force one key empty, run with dry-run:

```bash
POSTGRESQL_PASSWORD="" ./scripts/push-secrets.sh --dry-run
```

Expected output:
```
ERROR: The following required keys are missing or empty in secrets/.env.secrets:
  - POSTGRESQL_PASSWORD
```
Exit code should be 1.

- [ ] **Step 5: Test — dry-run with all required keys set prints key names without values**

First fill in at least the 14 required keys in `secrets/.env.secrets`, then:

```bash
./scripts/push-secrets.sh --dry-run
```

Expected output (no actual values printed, just key names):
```
All 14 required keys present.

── llmops/apikeys JSON (keys only) ──────────────────────────────────────
  POSTGRESQL_PASSWORD
  REDIS_PASSWORD
  LITELLM_MASTER_KEY
  LITELLM_SALT_KEY
  LANGFUSE_SECRET_KEY
  LANGFUSE_PUBLIC_KEY
  CLICKHOUSE_PASSWORD
  WEBUI_SECRET_KEY
  OPENAI_API_KEY
  ANTHROPIC_API_KEY
  GEMINI_API_KEY
  LANGFUSE_S3_ACCESS_KEY_ID
  LANGFUSE_S3_SECRET_ACCESS_KEY

── llmops/supabase JSON (keys only) ─────────────────────────────────────
  LITELLM_DB_URL

Dry run complete. Re-run without --dry-run to push to AWS.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/push-secrets.sh
git commit -m "feat: add push-secrets.sh to bootstrap AWS Secrets Manager from .env.secrets"
```

---

## Task 5: Run the real push and verify ExternalSecrets sync

> Skip this task if running in a test environment without AWS credentials.

- [ ] **Step 1: Fill in `secrets/.env.secrets` with real values**

Edit `secrets/.env.secrets`. All 14 required keys must be non-empty. For `LITELLM_DB_URL`, use:

```
LITELLM_DB_URL=postgres://postgres:<your-POSTGRESQL_PASSWORD>@postgresql-primary.postgresql.svc.cluster.local:5432/postgres
```

- [ ] **Step 2: Run the push**

```bash
./scripts/push-secrets.sh
```

Expected output:
```
All 14 required keys present.
Pushing llmops/apikeys...
  ARN: arn:aws:secretsmanager:ap-southeast-1:...:secret:llmops/apikeys-...  VersionId: <uuid>
Pushing llmops/supabase...
  ARN: arn:aws:secretsmanager:ap-southeast-1:...:secret:llmops/supabase-...  VersionId: <uuid>

Done at 2026-06-02T...Z.
ExternalSecrets will resync within ~60s. Watch with:
  kubectl get externalsecrets -A -w
```

- [ ] **Step 3: Watch ExternalSecrets resync**

```bash
kubectl get externalsecrets -A
```

Expected: all rows show `STATUS: SecretSynced` and `READY: True` within 60 seconds.

- [ ] **Step 4: Verify pods recover**

```bash
kubectl get pods -A | grep -v " Running \| Completed " | grep -v "^kube-system\|^NAMESPACE"
```

Expected: no output (all pods running).

---

## Self-Review

**Spec coverage:**
- ✅ `secrets/.env.example` with all 16 apikeys + 1 supabase key, required/optional marked, services documented — Task 2
- ✅ `secrets/.env.secrets` gitignored template — Task 3
- ✅ `scripts/push-secrets.sh` — validates 14 required, builds 2 JSON objects, upserts to AWS SM — Task 4
- ✅ `.gitignore` updated — Task 1
- ✅ Optional OIDC keys included only when non-empty — Task 4 Step 1 (Python block)
- ✅ `--dry-run` flag for safe testing without AWS credentials — Task 4

**Placeholder scan:** No TBDs, all code blocks complete, all commands have expected output.

**Type consistency:** `REQUIRED_KEYS` array in bash matches the 14-key list in the spec exactly. Python dict keys in `push-secrets.sh` match `.env.example` key names exactly.
