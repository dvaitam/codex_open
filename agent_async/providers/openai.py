from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json, http_post_json


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, api_key: str | None = None, system_prompt: str | None = None):
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY")
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
            "2.  **File Writing:** To create or overwrite a file, ALWAYS use this exact `cat` with a here-doc syntax. It is the most reliable method.\n"
            '    ```json\n'
            '    {"type": "run", "cmd": "cat > path/to/your_file.js <<EOF\\n// your file content here...\\n// ...more content...\\nEOF", "thought": "I am writing the full content to the file."}\n'
            '    ```\n'
            "    - **IMPORTANT**: The `EOF` marker must be on its own line. The `\\n` is critical.\n"
            "3.  **File Reading:** Use `head -n 100 <file>` or `grep <pattern> <file>`. Avoid `cat` on large files.\n"
            "4.  **No Human:** You have no human to ask for help. Discover information via commands.\n"
            "5.  **Finish:** When the task is complete, reply with `{\"type\":\"done\", \"message\":\"I have completed the task.\"}`."
        ).strip()

    async def complete(self, model: str, messages: List[Message]) -> str:
        if not self.api_key:
            raise RuntimeError("OpenAI API key required for completion")
        if not model:
            model = "gpt-4o-mini"

        chat_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            chat_messages.append({"role": role, "content": content})

        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = {"model": model, "messages": chat_messages}
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
            # Retry without temperature if server complains; we already omit, but keep guard
            if "temperature" in (msg or "").lower():
                body.pop("temperature", None)
                data = await http_post_json(
                    url,
                    body,
                    headers=headers,
                    timeout=90,
                    retries=1,
                    backoff=1.5,
                    debug=debug_flag,
                )
                if not isinstance(data.get("error"), dict):
                    # fall through to parse
                    pass
                else:
                    msg = data["error"].get("message") or str(data["error"])[:200]
                    raise RuntimeError(f"OpenAI API error: {msg}")
            else:
                raise RuntimeError(f"OpenAI API error: {msg}")
        choices = data.get("choices") or []
        if choices:
            choice = choices[0]
            if isinstance(choice, dict):
                msg = choice.get("message") or {}
                if isinstance(msg, dict) and msg.get("content"):
                    return str(msg["content"])
                if choice.get("text"):
                    return str(choice.get("text"))
        raise RuntimeError("OpenAI completion: no text in response")

    async def list_models(self) -> list[str]:
        try:
            if not self.api_key:
                raise RuntimeError("OpenAI API key required")
            url = "https://api.openai.com/v1/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            data = await http_get_json(url, headers=headers)
            items = [it.get("id") for it in (data.get("data") or []) if isinstance(it, dict) and it.get("id")]
            # Heuristic: prefer chat-capable models first
            preferred = [m for m in items if any(k in m for k in ("gpt-4", "gpt-4o", "o3", "o4", "chat"))]
            others = [m for m in items if m not in preferred]
            out = sorted(set(preferred)) + sorted(set(others))
            if out:
                return out
        except Exception:
            pass
        # Fallback list for offline/dev environments
        return [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4.1",
            "gpt-4.1-mini",
            "o3-mini",
        ]
