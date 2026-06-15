import re
import openai
from analytics_agent.config import OLLAMA_BASE_URL, OLLAMA_TIMEOUT


class OllamaError(Exception):
    pass


_client = openai.OpenAI(
    base_url=f"{OLLAMA_BASE_URL}/v1",
    api_key="ollama",
)

MODEL = "qwen2.5-coder:7b"


def chat(messages: list[dict], model: str = MODEL) -> str:
    try:
        response = _client.chat.completions.create(
            model=model,
            messages=messages,
            timeout=OLLAMA_TIMEOUT,
        )
        return response.choices[0].message.content
    except openai.APITimeoutError:
        raise OllamaError(f"Ollama timed out after {OLLAMA_TIMEOUT}s")
    except openai.APIConnectionError as e:
        raise OllamaError(f"Ollama connection failed: {e}")


def strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:sql)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    return match.group(1).strip() if match else text
