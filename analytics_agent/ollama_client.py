# Deprecated: use analytics_agent.gpt_client instead.
from analytics_agent.gpt_client import LLMClientError as OllamaError, chat, strip_fences

__all__ = ["OllamaError", "chat", "strip_fences"]
