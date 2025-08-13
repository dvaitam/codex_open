from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json, http_post_json


class XAIProvider(Provider):
    name = "xai"

    def __init__(self, api_key: str | None = None):
        key = api_key if api_key is not None else os.environ.get("XAI_API_KEY")
        super().__init__(key.strip() if key else None)

    async def complete(self, model: str, messages: List[Message]) -> str:
        if not self.api_key:
            raise RuntimeError("xAI API key required for completion")
        if not model:
            model = "grok-2-latest"

        chat_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            chat_messages.append({"role": role, "content": content})

        url = "https://api.x.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = {"model": model, "messages": chat_messages, "temperature": 0.1, "max_tokens": 800}
        debug_flag = bool(os.environ.get("AGENT_ASYNC_DEBUG_HTTP"))
        data = await http_post_json(
            url,
            body,
            headers=headers,
            timeout=90,
            retries=2,
            backoff=1.8,
            debug=debug_flag,
        )
        if isinstance(data.get("error"), dict):
            msg = data["error"].get("message") or str(data["error"])[:200]
            raise RuntimeError(f"xAI API error: {msg}")
        choices = data.get("choices") or []
        if choices:
            choice = choices[0]
            if isinstance(choice, dict):
                msg = choice.get("message") or {}
                if isinstance(msg, dict) and msg.get("content"):
                    return str(msg["content"])
                if choice.get("text"):
                    return str(choice.get("text"))
        raise RuntimeError("xAI completion: no text in response")

    async def list_models(self) -> list[str]:
        if not self.api_key:
            # Provide a hint list
            return ["grok-2-latest", "grok-2-mini", "grok-beta"]
        try:
            url = "https://api.x.ai/v1/models"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            }
            data = await http_get_json(url, headers=headers, debug=True)
            arr = data.get("data") or data.get("models") or []
            items = []
            for it in arr:
                if isinstance(it, dict):
                    mid = it.get("id") or it.get("name")
                    if mid:
                        items.append(mid)
            # Deduplicate while preserving order
            seen = set()
            out = []
            for m in items:
                if m not in seen:
                    seen.add(m)
                    out.append(m)
            if out:
                return out
        except Exception:
            pass
        return ["grok-2-latest", "grok-2-mini", "grok-beta"]
