from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, api_key: str | None = None):
        key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        super().__init__(key.strip() if key else None)

    async def complete(self, model: str, messages: List[Message]) -> str:
        raise RuntimeError("ClaudeProvider: network calls are disabled in this environment.")

    async def list_models(self) -> list[str]:
        if not self.api_key:
            # Fallback to a static list if no key
            return [
                "claude-3-5-sonnet-latest",
                "claude-3-opus-latest",
                "claude-3-5-haiku-latest",
            ]
        # Try Anthropic models endpoint (if available); fallback to static
        try:
            url = "https://api.anthropic.com/v1/models"
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            }
            data = await http_get_json(url, headers=headers)
            arr = data.get("data") or data.get("models") or []
            items = []
            for it in arr:
                if isinstance(it, dict):
                    items.append(it.get("id") or it.get("name"))
            return [m for m in items if m]
        except Exception:
            return [
                "claude-3-5-sonnet-latest",
                "claude-3-opus-latest",
                "claude-3-5-haiku-latest",
            ]
