# Deprecated: tests migrated to test_gpt_client.py
# Kept to ensure the ollama_client re-export shim works.
from analytics_agent.ollama_client import OllamaError, strip_fences


def test_ollama_error_is_llm_client_error():
    from analytics_agent.gpt_client import LLMClientError
    assert OllamaError is LLMClientError


def test_strip_fences_via_shim():
    assert strip_fences("```sql\nSELECT 1\n```") == "SELECT 1"
