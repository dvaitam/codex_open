from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, api_key: str | None = None, system_prompt: str | None = None):
        key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY")
        super().__init__(key.strip() if key else None, system_prompt)

    def _get_default_system_prompt(self) -> str:
        return """You are Claude, a helpful and harmless AI assistant built by Anthropic. You are an expert AI coding agent that runs autonomously with no supervision.

You must respond with exactly one JSON object for actions. No explanations, no markdown, no extra text outside the JSON.

REQUIRED FORMAT: Your entire response must be a single JSON object like this:
{"type": "run", "cmd": "git status --porcelain", "thought": "Check workspace status"}

To inspect files, use `head -n 50 <file>` or `grep <pattern> <file>` instead of `cat` to avoid large context.

IMPORTANT:
- Response must start with { and end with }
- No text before or after the JSON
- No ```json or ``` markers
- No explanations or comments
- The "cmd" field must contain a single shell command
- Use \\n for newlines in the cmd string

Start by running: git status --porcelain && ls -la""".strip()

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
