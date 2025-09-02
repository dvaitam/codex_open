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
        return (
            "You are an autonomous AI coding agent. Your goal is to complete the task by executing shell commands.\n\n"
            "**RESPONSE FORMAT**\n"
            "- Respond with EXACTLY one JSON object and nothing else.\n"
            '- The JSON object must have this schema:\n'
            '  {"type": "run" | "message" | "done", "cmd?": string, "message?": string, "thought": string}\n\n'
            "**RULES**\n"
            "1.  **JSON Only:** Your entire response must be a single, valid JSON object. No markdown, no commentary, no text outside the JSON.\n"
            "2.  File edits: Prefer minimal in-place edits to save tokens. Use full-file here-doc only when necessary.\n"
            "    - Preferred (portable): Python to read/modify/write a file (use replace/regex/insert).\n"
            "      If available, you may call the helper: python3 ../../agent_async/scripts/edit.py replace|regex|insert_after|ensure_block ...\n"
            "    - Full rewrite (when needed):\n"
            "      cat > path/to/file <<EOF\\n...content...\\nEOF\n"
            "3.  **Output Truncation:** Only the last 200 lines of each command's combined stdout/stderr are provided back to you in the conversation context. Prefer commands that focus output (tail/grep/rg).\n"
            "4.  **File Reading:** Use head -n 100 <file> or grep <pattern> <file>. Avoid cat on large files.\n"
            "5.  **No Human:** You have no human to ask for help. Discover information via commands.\n"
            "6.  **Finish:** When the task is complete, reply with {\"type\":\"done\", \"message\":\"I have completed the task.\"}."
        ).strip()

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
