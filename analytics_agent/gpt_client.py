import re
import openai
from analytics_agent.config import GPT_MODEL, GPT_TIMEOUT


class LLMClientError(Exception):
    pass


_client: openai.OpenAI | None = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = openai.OpenAI()  # reads OPENAI_API_KEY from env
    return _client


def chat(messages: list[dict], model: str = GPT_MODEL) -> str:
    try:
        response = _get_client().chat.completions.create(
            model=model,
            messages=messages,
            timeout=GPT_TIMEOUT,
        )
        return response.choices[0].message.content
    except openai.APITimeoutError:
        raise LLMClientError(f"GPT timed out after {GPT_TIMEOUT}s")
    except openai.APIConnectionError as e:
        raise LLMClientError(f"GPT connection failed: {e}")
    except openai.AuthenticationError as e:
        raise LLMClientError(f"GPT authentication failed: {e}")


def strip_fences(text: str) -> str:
    text = text.strip()
    match = re.match(r"^```(?:sql)?\s*\n?(.*?)\n?```$", text, re.DOTALL)
    return match.group(1).strip() if match else text
