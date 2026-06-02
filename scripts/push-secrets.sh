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

echo "All 12 required keys present."

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
}

# Always include S3 keys (even empty) — Langfuse chart requires them in the secret
for k in ("LANGFUSE_S3_ACCESS_KEY_ID", "LANGFUSE_S3_SECRET_ACCESS_KEY"):
    d[k] = os.environ.get(k, "")

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
