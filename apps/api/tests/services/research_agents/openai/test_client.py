from __future__ import annotations

from app.services.research_agents.openai.client import OpenAIChatClient


def test_openai_chat_client_is_unavailable_without_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    client = OpenAIChatClient(api_key=None)

    assert client.is_available is False


def test_openai_chat_client_uses_environment_api_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sandbox-test-key")

    client = OpenAIChatClient(api_key=None)

    assert client.is_available is True
