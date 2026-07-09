from __future__ import annotations

from collections.abc import Sequence
import json
import os
import urllib.request


class OpenAIChatClient:
    def __init__(self, *, api_key: str | None = None, base_url: str = "https://api.openai.com/v1") -> None:
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._base_url = base_url.rstrip("/")

    @property
    def is_available(self) -> bool:
        if self._api_key:
            return True
        self._api_key = os.getenv("OPENAI_API_KEY")
        return bool(self._api_key)

    def create_chat_completion(
        self,
        *,
        model: str,
        messages: Sequence[dict[str, str]],
        temperature: float,
        response_format: dict[str, str] | None = None,
    ) -> dict[str, object]:
        if not self._api_key:
            self._api_key = os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")

        payload: dict[str, object] = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self._base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            response_body = response.read().decode("utf-8")

        decoded = json.loads(response_body)
        if not isinstance(decoded, dict):
            raise RuntimeError("OpenAI response payload is invalid")
        return decoded
