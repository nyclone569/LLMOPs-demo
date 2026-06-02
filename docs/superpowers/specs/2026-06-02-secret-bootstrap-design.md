# Secret Bootstrap Workflow — Design Spec

**Date:** 2026-06-02
**Status:** Approved

## Problem

After a `terraform destroy` + `terraform apply`, all AWS Secrets Manager values are wiped. There is no single place that lists which keys are required, which service uses each key, or how to repopulate them. Developers have to hunt across 5 ExternalSecret YAMLs and 5 Helm values files to reconstruct the full key list — and any missing key silently causes `CreateContainerConfigError` pod failures.

## Goal

One file to fill in, one command to run — and the cluster has all secrets it needs.

## Architecture

Two AWS Secrets Manager paths remain unchanged (no changes to ExternalSecret YAMLs):

| AWS SM Path | K8s Secret Name | Used In Namespaces |
|---|---|---|
| `llmops/apikeys` | `llmops-apikeys-secret` | langfuse, litellm, open-webui, postgresql, redis |
| `llmops/supabase` | `llmops-supabase-secret` | langfuse, litellm |

New files added to the repo:

```
secrets/
  .env.example      ← committed; every key documented with service + required/optional
  .env.secrets      ← gitignored; developer fills in values, never committed

scripts/
  push-secrets.sh   ← reads .env.secrets, validates required keys, pushes JSON to AWS SM
```

## Key Map

All keys that must exist in AWS Secrets Manager for the platform to start.

### `llmops/apikeys` (→ `llmops-apikeys-secret`)

| Key | Required | Used By |
|---|---|---|
| `POSTGRESQL_PASSWORD` | ✅ | postgresql chart, langfuse DB auth |
| `REDIS_PASSWORD` | ✅ | redis chart, langfuse redis, litellm cache |
| `LITELLM_MASTER_KEY` | ✅ | litellm gateway auth, open-webui OpenAI key |
| `LITELLM_SALT_KEY` | ✅ | litellm encryption |
| `LANGFUSE_SECRET_KEY` | ✅ | langfuse app secret + NextAuth secret, litellm callback |
| `LANGFUSE_PUBLIC_KEY` | ✅ | langfuse public API, litellm callback |
| `CLICKHOUSE_PASSWORD` | ✅ | langfuse ClickHouse DB |
| `WEBUI_SECRET_KEY` | ✅ | open-webui session signing |
| `OPENAI_API_KEY` | ✅ | litellm → OpenAI models |
| `ANTHROPIC_API_KEY` | ✅ | litellm → Anthropic models |
| `GEMINI_API_KEY` | ✅ | litellm → Gemini models |
| `LANGFUSE_S3_ACCESS_KEY_ID` | ✅ | langfuse S3 blob storage |
| `LANGFUSE_S3_SECRET_ACCESS_KEY` | ✅ | langfuse S3 blob storage |
| `OIDC_PROVIDER_URL` | ⬜ optional | open-webui SSO login |
| `OIDC_CLIENT_ID` | ⬜ optional | open-webui SSO login |
| `OIDC_CLIENT_SECRET` | ⬜ optional | open-webui SSO login |

### `llmops/supabase` (→ `llmops-supabase-secret`)

| Key | Required | Used By |
|---|---|---|
| `LITELLM_DB_URL` | ✅ | litellm spend tracking DB (`postgres://postgres:<POSTGRESQL_PASSWORD>@postgresql-primary.postgresql.svc.cluster.local:5432/postgres`) |

## `push-secrets.sh` Behaviour

1. Source `secrets/.env.secrets` (fail fast if file not found)
2. Check all 14 required keys are non-empty — print a clear error listing any missing keys and exit 1
3. Build JSON object for `llmops/apikeys` from the 16 apikeys fields
4. Build JSON object for `llmops/supabase` from the 1 supabase field
5. Run `aws secretsmanager put-secret-value` for each path (upsert — safe to re-run)
6. Print confirmation with timestamp

Optional keys (`OIDC_*`) are included in the JSON only when non-empty, so omitting them doesn't break the secret.

## `.gitignore` Change

Add `secrets/.env.secrets` to prevent accidental commit of real values.

## Developer Workflow (After Any `terraform apply`)

```bash
cp secrets/.env.example secrets/.env.secrets   # first time only
# fill in secrets/.env.secrets with real values
./scripts/push-secrets.sh                       # push to AWS SM
# ExternalSecrets auto-sync within ~60s, then pods recover
```

## What Does NOT Change

- ExternalSecret YAMLs — no changes needed
- ClusterSecretStore — no changes needed
- Helm values files — no changes needed
- Terraform — no changes needed

The only manual step that was previously undocumented is now encoded in `.env.example` and automated by `push-secrets.sh`.
