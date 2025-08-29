from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json, http_post_json


class DeepseekProvider(Provider):
    name = "deepseek"

    def __init__(self, api_key: str | None = None):
        key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY")
        super().__init__(key.strip() if key else None)

    async def complete(self, model: str, messages: List[Message]) -> str:
        if not self.api_key:
            raise RuntimeError("Deepseek API key required for completion")
        if not model:
            model = "deepseek-chat"

        chat_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            chat_messages.append({"role": role, "content": content})

        url = "https://api.deepseek.com/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = {"model": model, "messages": chat_messages}
        data = await http_post_json(
            url,
            body,
            headers=headers,
            timeout=90,
            retries=2,
            backoff=1.8,
            debug=bool(os.environ.get("AGENT_ASYNC_DEBUG_HTTP")),
        )
        if isinstance(data.get("error"), dict):
            msg = data["error"].get("message") or str(data["error"])[:200]
            raise RuntimeError(f"Deepseek API error: {msg}")
        choices = data.get("choices") or []
        if choices:
            ch = choices[0]
            if isinstance(ch, dict):
                msg = ch.get("message") or {}
                if isinstance(msg, dict) and msg.get("content"):
                    return str(msg["content"])
                if ch.get("text"):
                    return str(ch.get("text"))
        raise RuntimeError("Deepseek completion: no text in response")

    async def list_models(self) -> list[str]:
        try:
            if not self.api_key:
                raise RuntimeError("Deepseek API key required")
            url = "https://api.deepseek.com/v1/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            data = await http_get_json(url, headers=headers)
            arr = data.get("data") or data.get("models") or []
            items = []
            for it in arr:
                if isinstance(it, dict):
                    mid = it.get("id") or it.get("name")
                    if mid:
                        items.append(mid)
            if items:
                return items
        except Exception:
            pass
        return ["deepseek-chat", "deepseek-reasoner"]

