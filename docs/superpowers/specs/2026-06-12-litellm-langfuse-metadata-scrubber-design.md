# LiteLLM → Langfuse metadata scrubber (deferred)

**Status:** Deferred. Not implemented. Trigger conditions below.

## Problem

LiteLLM passes virtual-key metadata into the Langfuse callback through
`kwargs["litellm_params"]["metadata"]`. That dict contains every key prefixed
`user_api_key_*` that LiteLLM populates for the request, including the
SHA-256 hash of the virtual key (`user_api_key_hash`) and, when set on the
key, the user's email (`user_api_key_user_email`).

We want the non-sensitive identifiers (`user_api_key_user_id`,
`user_api_key_team_alias`, `user_api_key_alias`) in Langfuse traces because
Requirements §5.1 lists them. We don't want the hash and email there.

LiteLLM has a `litellm_settings.standard_logging_payload_excluded_fields`
list, but it does **not** apply to the Langfuse callback. The Langfuse
integration reads `litellm_params["metadata"]` directly
(`/usr/lib/python3.13/site-packages/litellm/integrations/langfuse/langfuse.py`
line 284), bypassing the `StandardLoggingPayload` filter that
`excluded_fields` operates on.

The blanket `redact_user_api_key_info: true` flag works but strips *all*
`user_api_key_*` keys — including the identifiers we want to keep. That
breaks §5.1 and §5.3.

## Why this is deferred

1. The only sensitive value currently leaking is the virtual-key hash.
   `argocd/rbac-setup/setup-teams.py` does not populate `user_email` on any
   virtual key, so the email field is empty in every observation.
2. Langfuse access is admin-only (§7.1), so the hash isn't reaching ops
   users. §7.3's "separate normal logs from prompt traces" is honored.
3. The hash is a SHA-256 of the LiteLLM virtual key, not the upstream
   provider key. It is non-reversible to the upstream secret.

## Trigger to implement

Implement when **any** of the following becomes true:

- `argocd/rbac-setup/setup-teams.py` (or any other path) starts populating
  `user_email` on virtual keys → real PII would land in Langfuse.
- Langfuse access stops being admin-only.
- A LiteLLM upgrade extends `standard_logging_payload_excluded_fields` to
  cover the Langfuse callback (in which case implement nothing — just add
  the field to the YAML).

## Implementation when triggered

### Architecture

Place a `CustomLogger` subclass first in `litellm_settings.callbacks` so it
runs ahead of the `langfuse` and `prometheus` callbacks. Implement
`async_log_pre_api_call` and (for safety) `async_log_success_event` /
`async_log_failure_event` to delete the sensitive keys from
`kwargs["litellm_params"]["metadata"]` in place. All callbacks share the
same kwargs dict, so the mutation propagates.

### File layout

```
argocd/helm-values/litellm-callbacks/
  callbacks.py            # the CustomLogger subclass
```

Mounted into the LiteLLM pod via:

- a new ConfigMap `litellm-callbacks` (separate from the main config) holding
  `callbacks.py`
- chart `volumes` + `volumeMounts` entries that mount it at
  `/app/callbacks.py` (LiteLLM's working directory is `/app` so a top-level
  module path works)

### Helm values

```yaml
volumes:
  - name: callbacks
    configMap:
      name: litellm-callbacks
volumeMounts:
  - name: callbacks
    mountPath: /app/callbacks.py
    subPath: callbacks.py

config:
  litellm_settings:
    callbacks: ["callbacks.scrubber_instance"]   # FIRST in the list
    success_callback: ["langfuse", "prometheus"]
    failure_callback: ["langfuse", "prometheus"]
```

### Module sketch

```python
# argocd/helm-values/litellm-callbacks/callbacks.py
from litellm.integrations.custom_logger import CustomLogger

SENSITIVE_KEYS = ("user_api_key_hash", "user_api_key_user_email")

class MetadataScrubber(CustomLogger):
    def _scrub(self, kwargs):
        try:
            md = kwargs.get("litellm_params", {}).get("metadata") or {}
            for k in SENSITIVE_KEYS:
                md.pop(k, None)
        except Exception:
            # never crash a request because of scrubbing
            pass

    async def async_log_pre_api_call(self, model, messages, kwargs):
        self._scrub(kwargs)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._scrub(kwargs)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._scrub(kwargs)

scrubber_instance = MetadataScrubber()
```

### Verification

1. After rollout, query ClickHouse:
   ```sql
   SELECT mapKeys(metadata) FROM observations
   WHERE start_time > now() - INTERVAL 5 MINUTE LIMIT 1
   ```
   Expect neither `user_api_key_hash` nor `user_api_key_user_email`.
2. `user_api_key_user_id` and `user_api_key_team_alias` MUST still appear.
3. Run the traffic simulator briefly, watch for any 500 errors caused by the
   new callback — none expected because `_scrub` swallows exceptions.

### Risks

- Hook-ordering: this design assumes the callback in `callbacks` list runs
  before `success_callback` / `failure_callback`. If a future LiteLLM
  refactor changes that, the scrubber stops working silently. Verification
  step 1 above catches it.
- ConfigMap mount changes the pod template — triggers a rollout on every
  edit. Keep `callbacks.py` small and stable.
