# Internal LLMOps Platform Challenge Sample Solution

## 1. Domain Overview

This project simulates an internal enterprise LLM platform for a medium-to-large engineering organization.

The platform provides:

* A ChatGPT-like internal UI for employees
* A centralized LLM gateway for multiple model providers
* Observability for prompts, latency, cost, and errors
* Kubernetes-based deployment and GitOps-style operations
* Monitoring, logging, and tracing for both infrastructure and LLM workloads

The goal is to build a production-style LLMOps stack that supports secure internal AI usage while intentionally injecting realistic operational, cost, security, and reliability challenges.

---

## 2. Platform Architecture Design

### 2.1 Core Components

| Component                | Purpose                                                  | Suggested Tool                                      |
| ------------------------ | -------------------------------------------------------- | --------------------------------------------------- |
| Chat UI                  | Internal web interface for users                         | Open WebUI                                          |
| LLM Gateway              | Central API gateway for model routing and policy control | LiteLLM                                             |
| LLM Providers            | Backend model providers                                  | OpenAI, Azure OpenAI, Gemini, Bedrock, Ollama, vLLM |
| Prompt/LLM Observability | Trace prompts, responses, cost, latency, and errors      | Langfuse                                            |
| Cache                    | Cache repeated LLM responses or session metadata         | Redis                                               |
| Database                 | Store app metadata, users, prompts, traces               | PostgreSQL / Supabase                               |
| Kubernetes Platform      | Run the whole stack                                      | GKE / EKS / generic Kubernetes                      |
| Ingress                  | Expose UI and APIs securely                              | Nginx Ingress / Gateway API                         |
| Secrets                  | Manage API keys and credentials                          | External Secrets / Vault / Kubernetes Secrets       |
| Monitoring               | Infrastructure and service metrics                       | Prometheus + Grafana                                |
| Logging                  | Application and gateway logs                             | Loki or ELK                                         |
| CI/CD                    | Deploy and update platform components                    | ArgoCD / Jenkins / GitHub Actions                   |

---

## 3. Deployment Design

### 3.1 Kubernetes Namespaces

| Namespace         | Purpose                                          |
| ----------------- | ------------------------------------------------ |
| llm-platform      | Open WebUI, LiteLLM, Langfuse frontend/backend   |
| llm-observability | Prometheus, Grafana, Loki/ELK exporters          |
| llm-data          | PostgreSQL, Redis, object storage dependencies   |
| ingress-system    | Ingress controller or Gateway API                |
| external-secrets  | Secret synchronization from Vault/Secret Manager |

### 3.2 Services

| Service         | Exposure       | Notes                                |
| --------------- | -------------- | ------------------------------------ |
| open-webui      | Internal HTTPS | User-facing chat portal              |
| litellm-proxy   | Internal API   | Used by Open WebUI and internal apps |
| langfuse-web    | Internal HTTPS | Used by platform/admin users         |
| langfuse-worker | Private        | Background trace processing          |
| redis           | Private        | Cache/session/rate-limit support     |
| postgres        | Private        | Metadata and tracing database        |

### 3.3 Deployment Problems

**Compulsory:**

* **Multi-provider model routing**: LiteLLM must route traffic to at least 3 providers or model backends.
* **Secret management**: API keys must not be hardcoded in Helm values or Git.
* **Internal-only access**: Open WebUI, LiteLLM, and Langfuse must not be publicly exposed.
* **High availability**: LiteLLM and Open WebUI must run with at least 2 replicas.
* **Persistent storage**: Langfuse/PostgreSQL data must survive pod restarts.

**Optional chosen:**

* Add one local/private model backend using Ollama or vLLM for sensitive internal prompts.

---

## 4. LLM Gateway Design

### 4.1 LiteLLM Model Configuration

| Model Alias      | Backend Provider                             | Use Case                         |
| ---------------- | -------------------------------------------- | -------------------------------- |
| fast-chat        | Gemini Flash / GPT-4o-mini / Claude Haiku    | General internal chat            |
| coding-assistant | GPT-4.1 / Claude Sonnet / Gemini Pro         | Engineering and code review      |
| private-chat     | Ollama / vLLM                                | Sensitive internal prompts       |
| long-context     | Gemini Pro / Claude / GPT long-context model | Large document analysis          |
| fallback-model   | Cheaper backup model                         | Used when primary provider fails |

### 4.2 Gateway Policies

**Compulsory:**

* Define model aliases instead of exposing raw provider model names.
* Configure fallback model behavior.
* Enable request logging to Langfuse.
* Add per-user or per-team budget limits.
* Add rate limits for high-cost models.
* Add timeout and retry settings.

**Optional chosen:**

* Add routing rules:

  * Engineering users can access coding models.
  * General users can access only fast-chat.
  * Sensitive workloads prefer private-chat.

---

## 5. Observability Design

### 5.1 LLM Observability

Langfuse should collect:

| Metric / Trace Field    | Purpose                           |
| ----------------------- | --------------------------------- |
| user_id                 | Identify usage by user            |
| team                    | Group usage by department/team    |
| model                   | Track model usage                 |
| prompt_tokens           | Cost and load analysis            |
| completion_tokens       | Cost and output size analysis     |
| total_cost              | Budget visibility                 |
| latency_ms              | Performance monitoring            |
| status                  | Success/failure tracking          |
| error_message           | Debug failed requests             |
| prompt_template_version | Track prompt changes              |
| trace_id                | Correlate request across services |

### 5.2 Infrastructure Observability

Prometheus/Grafana should monitor:

| Component  | Metrics                                               |
| ---------- | ----------------------------------------------------- |
| Open WebUI | pod CPU/memory, HTTP request rate, errors             |
| LiteLLM    | request count, error rate, latency, provider failures |
| Langfuse   | queue depth, worker failures, ingestion latency       |
| Redis      | memory usage, evictions, connections                  |
| PostgreSQL | CPU, connections, storage, slow queries               |
| Kubernetes | pod restarts, HPA events, node pressure               |

### 5.3 Logging Requirements

Logs should support:

* Search by `trace_id`
* Search by `user_id`
* Search by model alias
* Search failed requests
* Search provider timeout and rate-limit errors

Sensitive data must be masked before logs are stored.

---

## 6. Data and Traffic Simulation

### 6.1 Simulated Users

| User Group  | Count | Usage Pattern                                 |
| ----------- | ----- | --------------------------------------------- |
| Engineering | 120   | Code generation, debugging, incident analysis |
| Product     | 50    | Requirement writing, summarization            |
| Support     | 80    | Customer response drafting                    |
| Operations  | 30    | Runbook and incident support                  |
| Executives  | 10    | Reports and business summaries                |

### 6.2 Request Pattern

| Traffic Type            | Description                                  |
| ----------------------- | -------------------------------------------- |
| Normal traffic          | 100 requests/min during working hours        |
| Burst traffic           | 1000 requests/min during incident simulation |
| Long-context traffic    | 5% of requests include large documents       |
| Expensive-model traffic | 15% of requests use high-cost models         |
| Failed-provider traffic | 3% simulated provider timeout/error          |
| Duplicate requests      | 2% repeated prompts from users               |

### 6.3 Traffic Problems

**Compulsory:**

* **Burst load**: Simulate traffic spike during incident window.
* **Provider failure**: One model provider returns intermittent errors.
* **Cost spike**: A small number of users consume high-token models heavily.
* **Long prompt pressure**: Some requests contain very large prompts.
* **Sensitive prompt leakage risk**: Some prompts contain fake secrets or PII-like values.

**Optional chosen:**

* Simulate one team accidentally using the most expensive model for all requests.

---

## 7. Security and Governance

### 7.1 Access Control

The platform must support:

* SSO/OIDC login for Open WebUI
* Admin-only access to Langfuse
* Internal network-only access to LiteLLM
* Role-based model access
* Audit logs for model usage

### 7.2 Secret Handling

**Compulsory:**

* Store provider API keys in a secret manager.
* Sync secrets to Kubernetes using External Secrets or equivalent.
* Do not commit API keys to Git.
* Rotate at least one provider key during the challenge.
* Verify pods reload or restart safely after secret rotation.

### 7.3 Data Protection

The system must:

* Mask secrets in logs.
* Prevent prompt/response logs from exposing fake PII.
* Define retention policy for traces and logs.
* Separate normal logs from LLM prompt traces.
* Restrict access to sensitive traces.

---

## 8. Reliability Challenge

### 8.1 Failure Scenarios

Inject the following failures:

| Failure                          | Expected Behavior                          |
| -------------------------------- | ------------------------------------------ |
| Primary model provider timeout   | LiteLLM retries or falls back              |
| Redis unavailable                | Platform still works with degraded caching |
| Langfuse unavailable             | Chat should continue, tracing can degrade  |
| One Open WebUI pod crashes       | Service remains available                  |
| One LiteLLM pod crashes          | API remains available                      |
| PostgreSQL high connection count | Alert fires before outage                  |

### 8.2 SLO Targets

| Service                      | SLO                                    |
| ---------------------------- | -------------------------------------- |
| Open WebUI availability      | 99.5%                                  |
| LiteLLM gateway availability | 99.9%                                  |
| P95 LiteLLM latency          | < 3 seconds excluding provider latency |
| LLM request success rate     | > 98%                                  |
| Trace ingestion delay        | < 60 seconds                           |
| Error alert detection        | < 5 minutes                            |

---

## 9. Feature Engineering / Analytics

Compute the following platform analytics from LiteLLM and Langfuse data.

### 9.1 Usage Features

| Feature                           | Description                                            |
| --------------------------------- | ------------------------------------------------------ |
| f_user_requests_1d                | Number of LLM requests per user in 1 day               |
| f_user_tokens_1d                  | Total tokens consumed per user in 1 day                |
| f_team_cost_7d                    | Total LLM cost per team in 7 days                      |
| f_model_error_rate_1h             | Error rate per model in 1 hour                         |
| f_provider_latency_p95_1h         | P95 latency per provider in 1 hour                     |
| f_expensive_model_ratio_1d        | Ratio of expensive-model requests per user/team        |
| f_prompt_secret_detected_count_1d | Number of prompts containing fake secrets/PII patterns |

### 9.2 Alerting Rules

Create alerts for:

* LiteLLM error rate > 5% for 5 minutes
* Provider timeout rate > 10% for 5 minutes
* Open WebUI unavailable
* Langfuse trace ingestion delay > 60 seconds
* PostgreSQL connection usage > 80%
* Redis memory usage > 80%
* Daily LLM cost exceeds budget
* One user/team consumes more than 30% of daily token budget
* Sensitive prompt detected in logs/traces

---

## 10. Generator / Simulator Configuration

```yaml
platform:
  cluster_provider: "gke"
  namespace: "llm-platform"
  ingress_type: "internal"
  replicas:
    open_webui: 2
    litellm: 3
    langfuse_web: 2
    langfuse_worker: 2

models:
  providers:
    - name: "openai"
      enabled: true
      failure_rate: 0.02
    - name: "gemini"
      enabled: true
      failure_rate: 0.03
    - name: "anthropic"
      enabled: true
      failure_rate: 0.01
    - name: "ollama"
      enabled: true
      failure_rate: 0.05

traffic:
  simulated_users: 290
  base_requests_per_min: 100
  burst_requests_per_min: 1000
  burst_windows:
    - "10:00-10:30"
    - "15:00-15:20"
  long_context_rate: 0.05
  duplicate_prompt_rate: 0.02
  provider_timeout_rate: 0.03
  expensive_model_request_rate: 0.15
  sensitive_prompt_rate: 0.01

cost:
  daily_budget_usd: 200
  team_budget_usd:
    engineering: 100
    product: 30
    support: 40
    operations: 20
    executives: 10
  alert_threshold_percent: 80

security:
  enable_sso: true
  enable_rbac: true
  enable_secret_masking: true
  secret_rotation_test: true
  trace_retention_days: 30
  log_retention_days: 14

observability:
  prometheus_enabled: true
  grafana_enabled: true
  loki_enabled: true
  langfuse_enabled: true
  alertmanager_enabled: true

chaos:
  provider_failure_test: true
  redis_failure_test: true
  langfuse_failure_test: true
  pod_restart_test: true
  postgres_connection_pressure_test: true

random_seed: 42
```

---

## 11. Deliverables

1. **Architecture document**

   * Explain the full platform design.
   * Include request flow from Open WebUI to LiteLLM to model provider.
   * Include observability flow to Langfuse, Prometheus, Grafana, and logs.

2. **Kubernetes deployment**

   * Helm values or manifests for Open WebUI, LiteLLM, Langfuse, Redis, and PostgreSQL.
   * Namespace and network policy design.
   * Internal ingress configuration.
   * HPA configuration for Open WebUI and LiteLLM.

3. **Secret management**

   * External Secrets or equivalent configuration.
   * API key injection into LiteLLM.
   * Secret rotation test result.

4. **LiteLLM configuration**

   * At least 3 model providers.
   * Model aliases.
   * Fallback rules.
   * Budget limits.
   * Rate limits.
   * Langfuse integration.

5. **Observability dashboards**

   * LiteLLM request rate, error rate, latency, and provider health.
   * Token usage and cost by user/team/model.
   * Open WebUI availability.
   * Langfuse ingestion health.
   * PostgreSQL and Redis health.

6. **Alerting rules**

   * Error rate alert.
   * Provider timeout alert.
   * Cost spike alert.
   * Sensitive prompt alert.
   * PostgreSQL/Redis resource alert.
   * Pod restart alert.

7. **Traffic simulator**

   * Generate synthetic LLM requests.
   * Simulate burst traffic.
   * Simulate provider failures.
   * Simulate expensive-model misuse.
   * Simulate fake sensitive prompts.

8. **Quality and reliability report**

   * Request success rate.
   * P95/P99 latency.
   * Cost per model/team/user.
   * Provider failure and fallback rate.
   * Alert trigger results.
   * Trace completeness in Langfuse.
   * Impact of Redis/Langfuse/provider failure tests.

9. **Write-up**

   * Explain architecture tradeoffs.
   * Explain why Open WebUI, LiteLLM, and Langfuse were selected.
   * Explain security and governance decisions.
   * Explain optional challenge choice.
   * Provide production improvement recommendations.

---

## 12. Implementation Tips

* Start with Docker Compose for local testing, then move to Kubernetes.
* Keep LiteLLM as the only direct path to external LLM providers.
* Do not let Open WebUI call provider APIs directly.
* Use model aliases instead of exposing provider-specific model names.
* Enable Langfuse tracing early so debugging is easier.
* Add fake users and teams to test cost attribution.
* Use deterministic seeds for repeatable traffic simulation.
* Use NetworkPolicy to restrict direct access to PostgreSQL, Redis, and LiteLLM.
* Use internal LoadBalancer or private Ingress for company-only access.
* Define dashboards before running chaos tests.
* Test degraded mode: Langfuse down should not break user chat.
* Test fallback mode: one provider down should not fully break LiteLLM.
* Define clear ownership: platform admin, security admin, model owner, app team.
 