import pytest
from unittest.mock import patch, MagicMock
from analytics_agent.gpt_client import chat, LLMClientError, strip_fences


def _mock_response(content: str):
    mock = MagicMock()
    mock.choices[0].message.content = content
    return mock


def _mock_client(content: str = "hello"):
    mock = MagicMock()
    mock.chat.completions.create.return_value = _mock_response(content)
    return mock


def test_chat_returns_content():
    with patch("analytics_agent.gpt_client._get_client", return_value=_mock_client("hello")):
        result = chat(messages=[{"role": "user", "content": "hi"}])
    assert result == "hello"


def test_chat_raises_on_timeout():
    import openai
    mock = MagicMock()
    mock.chat.completions.create.side_effect = openai.APITimeoutError(request=MagicMock())
    with patch("analytics_agent.gpt_client._get_client", return_value=mock):
        with pytest.raises(LLMClientError, match="timed out"):
            chat(messages=[{"role": "user", "content": "hi"}])


def test_chat_raises_on_connection_error():
    import openai
    mock = MagicMock()
    mock.chat.completions.create.side_effect = openai.APIConnectionError(request=MagicMock())
    with patch("analytics_agent.gpt_client._get_client", return_value=mock):
        with pytest.raises(LLMClientError, match="connection"):
            chat(messages=[{"role": "user", "content": "hi"}])


def test_chat_raises_on_auth_error():
    import openai
    mock = MagicMock()
    mock.chat.completions.create.side_effect = openai.AuthenticationError(
        message="invalid key", response=MagicMock(), body={}
    )
    with patch("analytics_agent.gpt_client._get_client", return_value=mock):
        with pytest.raises(LLMClientError, match="authentication"):
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
