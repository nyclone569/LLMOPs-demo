import pytest
from unittest.mock import patch, MagicMock
from analytics_agent.ollama_client import chat, OllamaError, strip_fences


def _mock_response(content: str):
    mock = MagicMock()
    mock.choices[0].message.content = content
    return mock


def test_chat_returns_content():
    with patch("analytics_agent.ollama_client._client") as mock_client:
        mock_client.chat.completions.create.return_value = _mock_response("hello")
        result = chat(messages=[{"role": "user", "content": "hi"}])
    assert result == "hello"


def test_chat_raises_ollama_error_on_timeout():
    import openai
    with patch("analytics_agent.ollama_client._client") as mock_client:
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(request=MagicMock())
        with pytest.raises(OllamaError, match="timed out"):
            chat(messages=[{"role": "user", "content": "hi"}])


def test_chat_raises_ollama_error_on_connection_error():
    import openai
    with patch("analytics_agent.ollama_client._client") as mock_client:
        mock_client.chat.completions.create.side_effect = openai.APIConnectionError(request=MagicMock())
        with pytest.raises(OllamaError, match="connection"):
            chat(messages=[{"role": "user", "content": "hi"}])


def test_strip_fences_removes_sql_block():
    raw = "```sql\nSELECT * FROM t\n```"
    assert strip_fences(raw) == "SELECT * FROM t"


def test_strip_fences_removes_plain_block():
    raw = "```\nSELECT 1\n```"
    assert strip_fences(raw) == "SELECT 1"


def test_strip_fences_passthrough_plain():
    assert strip_fences("SELECT 1") == "SELECT 1"


def test_strip_fences_strips_whitespace():
    assert strip_fences("  SELECT 1  ") == "SELECT 1"
