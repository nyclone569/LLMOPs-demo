# LLMOps Platform on AWS EKS

A self-hosted LLMOps platform built on AWS EKS, simulating 290 employees across 5 teams. Covers multi-provider LLM routing, cost control, privacy enforcement, full observability, and an AI analytics agent over NYC yellow cab data.

## Architecture

```
Open WebUI (chat UI)
    │
    ▼
LiteLLM (LLM gateway — HPA 3–10 replicas)
    ├── OpenAI / Gemini / Anthropic  (external)
    └── Ollama                       (in-cluster, for sensitive prompts)
    │
    ├── Langfuse     (LLM tracing)
    ├── Redis        (caching / rate-limit)
    └── PostgreSQL   (app metadata)

Prometheus + Grafana + Loki + Promtail  (infra & LLM observability)
Analytics Agent (Open WebUI Pipe)       (NYC taxi data, DuckDB on S3)
```

Infrastructure is provisioned with a 4-layer Terraform stack (VPC → EKS → cluster addons → app resources) and deployed via ArgoCD App-of-Apps — zero manual steps from an empty cluster to a running platform.

## Platform Engineering Highlights

### Autoscaling & High Availability
- **LiteLLM HPA**: scales 3 → 8 replicas on CPU ≥ 70% or Memory ≥ 90% (via Metrics Server)
- **PodDisruptionBudget** (`minAvailable: 1`) ensures the gateway stays up during node drains and rolling updates
- **Latency-based routing** across providers; 2 retries + per-alias fallback chains (e.g. `coding-assistant` → `gpt-4o-mini`) with 60s provider cooldown

### Load Balancer & Ingress
- **AWS ALB Controller** with `scheme: internal` — Open WebUI and LiteLLM are never publicly reachable
- Target type `ip` for direct pod routing (no NodePort hop); health checks on `/health/liveness`
- LiteLLM and Langfuse each get their own internal ALB; Open WebUI is behind an ingress-nginx instance

### Network Isolation
- **NetworkPolicy** per namespace — least-privilege ingress and egress:
  - Redis and PostgreSQL only accept connections from `litellm` and `langfuse`
  - LiteLLM egress to external LLM APIs is whitelisted on port 443/80, excluding all private CIDRs (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
  - Open WebUI can only reach LiteLLM; it cannot talk directly to any provider or database

### Secret Management & Identity
- **IRSA** on every service account that touches AWS — no static access keys anywhere in the cluster
- **External Secrets Operator** syncs from AWS Secrets Manager; pods never mount raw Kubernetes Secrets
- All pods run as non-root (`runAsUser: 65534`), no privilege escalation, all Linux capabilities dropped

### GitOps Deployment
- ArgoCD **syncWave ordering** — ESO, AWS LB Controller, and cert-manager are healthy before any app wave deploys
- Single `kubectl apply -f argocd/root-app.yaml` bootstraps the entire platform from an empty cluster

## Repo Structure

```
terraform/
  vpc/          # VPC, subnets, NAT
  eks/          # EKS cluster, node groups
  bootstrap/    # ArgoCD, ESO, AWS LB Controller, EBS CSI, cert-manager
  app/          # S3 bucket, IAM roles for app workloads
argocd/
  apps/         # ArgoCD Application manifests (App-of-Apps)
  helm-values/  # Per-service Helm values
  monitoring/   # Prometheus rules, Grafana dashboards, service monitors
  external-secrets/
  traffic-simulator/
analytics_agent/ # Standalone 3-agent pipeline (local / CI use)
openwebui/       # Open WebUI Pipe (filter_analytics.py)
docker-compose/  # Local dev setup
tests/
docs/
```

## Quick Start

### Local (Docker Compose)

```bash
cd docker-compose
cp .env.example .env      # fill in your API keys
docker compose up -d
```

Open WebUI → http://localhost:8080 · Langfuse → http://localhost:3000

See [`docker-compose/README.md`](docker-compose/README.md) for full setup including Ollama model pulls and Langfuse key wiring.

### Production (EKS)

```bash
# 1. Provision infrastructure (run each layer in order)
cd terraform/vpc && terraform apply
cd ../eks       && terraform apply
cd ../bootstrap && terraform apply   # installs ArgoCD + cluster addons
cd ../app       && terraform apply   # S3, IRSA roles

# 2. Push ArgoCD root app — everything else syncs automatically
kubectl apply -f argocd/root-app.yaml
```

Prerequisites: `terraform >= 1.7`, `kubectl >= 1.29`, `helm >= 3.13`, AWS credentials with sufficient IAM permissions.

See [`docs/RUNBOOK.md`](docs/RUNBOOK.md) for day-2 operations and incident response.

## Analytics Agent

The analytics agent runs as an Open WebUI Pipe (`openwebui/filter_analytics.py`). It classifies incoming messages as `analytics` or `chat`, then for analytics queries runs a three-step pipeline:

1. **Supervisor** — selects the right table(s) from a schema registry on S3
2. **Query agent** — generates DuckDB SQL; retries once on validation or execution failure
3. **Summarize agent** — streams a business-language summary; Vega-Lite chart renders inline

The standalone agent (`analytics_agent/`) can also be run locally against a local Ollama instance without the full EKS stack.

```bash
cp .env.example .env   # set ANALYTICS_S3_BUCKET, OLLAMA_BASE_URL
python app.py
```
