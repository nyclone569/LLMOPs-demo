# ExternalSecret for Supabase credentials
resource "kubernetes_manifest" "external_secret_supabase" {
  manifest = {
    apiVersion = "external-secrets.io/v1beta1"
    kind       = "ExternalSecret"
    metadata = {
      name      = "llmops-supabase-secret"
      namespace = var.secrets_namespace
    }
    spec = {
      refreshInterval = "1h"
      secretStoreRef = {
        name = "aws-secrets-manager"
        kind = "ClusterSecretStore"
      }
      target = {
        name           = "llmops-supabase-secret"
        creationPolicy = "Owner"
        template = {
          engineVersion = "v2"
          data = {
            LANGFUSE_DB_URL = "{{ .langfuse_db_url }}"
            LITELLM_DB_URL  = "{{ .litellm_db_url }}"
          }
        }
      }
      dataFrom = [
        {
          extract = {
            key = var.supabase_secret_name
          }
        }
      ]
    }
  }
}

# ExternalSecret for API keys
resource "kubernetes_manifest" "external_secret_api_keys" {
  manifest = {
    apiVersion = "external-secrets.io/v1beta1"
    kind       = "ExternalSecret"
    metadata = {
      name      = "llmops-apikeys-secret"
      namespace = var.secrets_namespace
    }
    spec = {
      refreshInterval = "1h"
      secretStoreRef = {
        name = "aws-secrets-manager"
        kind = "ClusterSecretStore"
      }
      target = {
        name           = "llmops-apikeys-secret"
        creationPolicy = "Owner"
        template = {
          engineVersion = "v2"
          data = {
            OPENAI_API_KEY      = "{{ .openai_api_key }}"
            ANTHROPIC_API_KEY   = "{{ .anthropic_api_key }}"
            GEMINI_API_KEY      = "{{ .gemini_api_key }}"
            LITELLM_MASTER_KEY  = "{{ .litellm_master_key }}"
            LITELLM_SALT_KEY    = "{{ .litellm_salt_key }}"
            LANGFUSE_PUBLIC_KEY = "{{ .langfuse_public_key }}"
            LANGFUSE_SECRET_KEY = "{{ .langfuse_secret_key }}"
            REDIS_PASSWORD      = "{{ .redis_password }}"
            WEBUI_SECRET_KEY    = "{{ .webui_secret_key }}"
            POSTGRESQL_PASSWORD          = "{{ .postgresql_password }}"
            CLICKHOUSE_PASSWORD          = "{{ .clickhouse_password }}"
            LANGFUSE_S3_ACCESS_KEY_ID    = "{{ .langfuse_s3_access_key_id }}"
            LANGFUSE_S3_SECRET_ACCESS_KEY = "{{ .langfuse_s3_secret_access_key }}"
            OIDC_PROVIDER_URL            = "{{ .oidc_provider_url }}"
            OIDC_CLIENT_ID               = "{{ .oidc_client_id }}"
            OIDC_CLIENT_SECRET           = "{{ .oidc_client_secret }}"
          }
        }
      }
      dataFrom = [
        {
          extract = {
            key = var.api_keys_secret_name
          }
        }
      ]
    }
  }
}

# ExternalSecret for Redis connection details
# This creates a Kubernetes secret with Redis endpoint from Terraform
resource "kubernetes_secret" "redis_connection" {
  metadata {
    name      = "llmops-redis-secret"
    namespace = var.secrets_namespace
  }

  data = {
    REDIS_HOST     = aws_elasticache_cluster.redis.cache_nodes[0].address
    REDIS_PORT     = tostring(aws_elasticache_cluster.redis.port)
    REDIS_ENDPOINT = "${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.port}"
    REDIS_URL      = "redis://${aws_elasticache_cluster.redis.cache_nodes[0].address}:${aws_elasticache_cluster.redis.port}"
  }

  type = "Opaque"
}
