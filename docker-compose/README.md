# LLMOps Platform - Docker Compose Setup

This is a local development setup for the Internal LLMOps Platform using Docker Compose.

## Architecture

```
┌─────────────┐
│ Open WebUI  │ (Port 8080) - Chat Interface
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  LiteLLM    │ (Port 4000) - LLM Gateway
└──────┬──────┘
       │
       ├─────────────┬──────────────┬─────────────┐
       ▼             ▼              ▼             ▼
   OpenAI        Gemini       Anthropic      Ollama (Local)
                                              (Port 11434)
       │
       ├──────────► Langfuse (Port 3000) - Observability
       │
       ├──────────► Redis (Port 6379) - Cache
       │
       └──────────► PostgreSQL (Port 5432) - Database
```

## Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- At least 8GB RAM available for Docker
- API keys for at least one LLM provider (OpenAI, Gemini, or Anthropic)

## Quick Start

### 1. Clone and Setup Environment

```bash
cd /home/sirfenrir/Documents/LLMOPs/docker-compose

# Copy environment template
cp .env.example .env

# Edit .env with your API keys
nano .env  # or use your preferred editor
```

### 2. Generate Secure Secrets

```bash
# Generate random secrets for Langfuse
echo "NEXTAUTH_SECRET=$(openssl rand -hex 32)" >> .env
echo "SALT=$(openssl rand -hex 32)" >> .env
echo "ENCRYPTION_KEY=$(openssl rand -hex 16)" >> .env
```

### 3. Start the Platform

```bash
# Start all services
docker-compose up -d

# Watch logs
docker-compose logs -f

# Or watch specific service
docker-compose logs -f litellm
```

### 4. Initialize Services

**Wait for services to be healthy (2-3 minutes):**

```bash
# Check service health
docker-compose ps
```

**Pull Ollama models (for private-chat):**

```bash
# Pull a small model for testing
docker exec llmops-ollama ollama pull llama3.2

# Optional: Pull additional models
docker exec llmops-ollama ollama pull qwen2.5:7b
```

### 5. Access the Platform

| Service | URL | Default Credentials |
|---------|-----|---------------------|
| **Open WebUI** | http://localhost:8080 | Create account on first visit |
| **Langfuse** | http://localhost:3000 | Create account on first visit |
| **LiteLLM** | http://localhost:4000 | API Key: `sk-1234567890abcdef` |
| **Ollama** | http://localhost:11434 | No auth |

### 6. Configure Langfuse Integration

1. Open Langfuse at http://localhost:3000
2. Create an account and login
3. Create a new project (e.g., "LLMOps Platform")
4. Go to Settings → API Keys
5. Copy the Public Key and Secret Key
6. Update your `.env` file:
   ```bash
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   ```
7. Restart LiteLLM:
   ```bash
   docker-compose restart litellm
   ```

### 7. Test the Setup

```bash
# Test LiteLLM health
curl http://localhost:4000/health

# Test model availability
curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer sk-1234567890abcdef"

# Test a chat completion
curl http://localhost:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-1234567890abcdef" \
  -d '{
    "model": "fast-chat",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Service Details

### Open WebUI (Port 8080)
- **Purpose**: User-facing chat interface
- **Database**: PostgreSQL (openwebui database)
- **Backend**: Points to LiteLLM proxy
- **Features**: Multi-user support, chat history, model selection

### LiteLLM (Port 4000)
- **Purpose**: Central LLM gateway with routing, fallbacks, and observability
- **Config**: `litellm-config.yaml`
- **Features**:
  - Multi-provider routing (OpenAI, Gemini, Anthropic, Ollama)
  - Automatic fallbacks
  - Rate limiting and budgets
  - Redis caching
  - Langfuse tracing

### Langfuse (Port 3000)
- **Purpose**: LLM observability and tracing
- **Database**: PostgreSQL (langfuse database)
- **Features**:
  - Request/response logging
  - Cost tracking
  - Latency monitoring
  - User analytics

### PostgreSQL (Port 5432)
- **Databases**:
  - `langfuse` - Langfuse traces and metadata
  - `litellm` - LiteLLM usage data
  - `openwebui` - Open WebUI user data and chats
- **Credentials**: See `.env` file

### Redis (Port 6379)
- **Purpose**: Response caching and rate limiting
- **Config**: 512MB max memory with LRU eviction

### Ollama (Port 11434)
- **Purpose**: Local LLM inference for private/sensitive workloads
- **Models**: Pull models as needed with `docker exec`

## Model Aliases

Configure these in Open WebUI or use directly via API:

| Alias | Providers | Use Case |
|-------|-----------|----------|
| `fast-chat` | Gemini Flash, GPT-4o-mini, Claude Haiku | General chat |
| `coding-assistant` | GPT-4o, Claude Sonnet, Gemini Pro | Code generation |
| `private-chat` | Ollama (local) | Sensitive data |
| `long-context` | Gemini Pro, Claude Sonnet | Document analysis |
| `fallback-model` | Gemini Flash | Backup when others fail |

## Configuration

### Adding New Models

Edit `litellm-config.yaml`:

```yaml
model_list:
  - model_name: my-new-model
    litellm_params:
      model: gpt-4o
      api_key: os.environ/OPENAI_API_KEY
```

Then restart:
```bash
docker-compose restart litellm
```

### Adjusting Rate Limits

Edit `litellm-config.yaml` under `model_settings`:

```yaml
model_settings:
  - model_name: coding-assistant
    litellm_params:
      rpm: 50  # requests per minute
      tpm: 100000  # tokens per minute
```

### Budget Configuration

Budgets are configured in `litellm-config.yaml`:

```yaml
general_settings:
  max_budget: 6000  # USD per month
  budget_duration: 30d
```

For per-user/team budgets, use the LiteLLM Admin API (documentation: https://docs.litellm.ai/docs/proxy/virtual_keys)

## Monitoring

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f litellm
docker-compose logs -f langfuse-server
docker-compose logs -f open-webui
```

### Check Resource Usage

```bash
docker stats
```

### Database Access

```bash
# Connect to PostgreSQL
docker exec -it llmops-postgres psql -U llmops -d langfuse

# Connect to Redis
docker exec -it llmops-redis redis-cli
```

## Troubleshooting

### Services Won't Start

```bash
# Check logs
docker-compose logs

# Restart specific service
docker-compose restart <service-name>

# Full restart
docker-compose down
docker-compose up -d
```

### LiteLLM Can't Connect to Providers

1. Check API keys in `.env`
2. Verify keys are valid:
   ```bash
   # Test OpenAI key
   curl https://api.openai.com/v1/models \
     -H "Authorization: Bearer $OPENAI_API_KEY"
   ```
3. Check LiteLLM logs:
   ```bash
   docker-compose logs litellm
   ```

### Langfuse Not Receiving Traces

1. Verify Langfuse keys are set in `.env`
2. Check Langfuse is healthy:
   ```bash
   curl http://localhost:3000/api/public/health
   ```
3. Restart LiteLLM after updating keys:
   ```bash
   docker-compose restart litellm
   ```

### Open WebUI Can't Connect to LiteLLM

1. Check LiteLLM is running:
   ```bash
   curl http://localhost:4000/health
   ```
2. Verify `LITELLM_MASTER_KEY` matches in both services
3. Check Open WebUI logs:
   ```bash
   docker-compose logs open-webui
   ```

### Ollama Models Not Loading

```bash
# Check Ollama is running
curl http://localhost:11434/api/tags

# Pull model manually
docker exec llmops-ollama ollama pull llama3.2

# Check Ollama logs
docker-compose logs ollama
```

### Database Connection Issues

```bash
# Check PostgreSQL is healthy
docker-compose ps postgres

# Check database exists
docker exec llmops-postgres psql -U llmops -c "\l"

# Recreate databases
docker-compose down -v  # WARNING: Deletes all data
docker-compose up -d
```

## Stopping the Platform

```bash
# Stop services (keeps data)
docker-compose stop

# Stop and remove containers (keeps data)
docker-compose down

# Stop and remove everything including data
docker-compose down -v
```

## Next Steps

1. **Test the full flow**: Open WebUI → LiteLLM → Provider → Langfuse
2. **Create test users** in Open WebUI with different roles
3. **Generate traffic** using the traffic simulator (see `../traffic-simulator/`)
4. **Set up dashboards** in Langfuse for cost and usage tracking
5. **Migrate to Kubernetes** once local setup is validated

## Production Considerations

This setup is for **local development only**. For production:

- [ ] Use managed PostgreSQL (RDS, Cloud SQL)
- [ ] Use managed Redis (ElastiCache, Memorystore)
- [ ] Implement proper secret management (Vault, Secret Manager)
- [ ] Add TLS/SSL certificates
- [ ] Configure SSO/OIDC authentication
- [ ] Set up proper monitoring (Prometheus, Grafana)
- [ ] Implement backup and disaster recovery
- [ ] Use Kubernetes for high availability
- [ ] Configure network policies and firewalls
- [ ] Set up log aggregation (Loki, ELK)

## Resources

- [LiteLLM Documentation](https://docs.litellm.ai/)
- [Open WebUI Documentation](https://docs.openwebui.com/)
- [Langfuse Documentation](https://langfuse.com/docs)
- [Ollama Documentation](https://ollama.ai/docs)
