from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json, http_post_json


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
            "\n\n**Safety and cleanliness**\n"
            "- Before finishing, ensure no compiled binaries or build artifacts remain in the working tree or staged for commit. Remove typical artifacts (e.g., __pycache__/, *.pyc, dist/, build/, node_modules/, *.o, *.so, *.dll, *.exe, target/, *.class) or add appropriate .gitignore entries and run a safe cleanup (e.g., `git clean -fdX` after confirming ignores). Do not include built artifacts in any commits or PRs.\n"
            "- Cleanup step: Always run a cleanup command before replying with type 'done'. Prefer `git clean -fdX` to remove ignored build outputs. If artifacts aren't ignored, explicitly delete common build directories/files (e.g., `rm -rf -- dist build target out .venv .tox .pytest_cache node_modules */bin */obj *.o *.so *.dll *.exe *.class __pycache__`)."
        ).strip()

    async def complete(self, model: str, messages: List[Message]) -> str:
        if not self.api_key:
            raise RuntimeError("Anthropic API key required for completion")
        if not model:
            model = "claude-3-5-sonnet-latest"

        # Separate system messages to pass as system instruction
        sys_msgs = [m.get("content", "") for m in messages if m.get("role") == "system"]
        system_instruction = "\n\n".join([s for s in sys_msgs if isinstance(s, str)]).strip() or None

        # Map messages to Anthropic format (user/assistant roles only)
        anthro_messages = []
        for m in messages:
            role = m.get("role")
            if role == "system":
                continue
            if role not in ("user", "assistant"):
                role = "user"
            anthro_messages.append({"role": role, "content": m.get("content", "")})

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": model,
            "messages": anthro_messages,
            "max_tokens": 8000,
            # modest temperature for more deterministic tooling
            "temperature": 0.1,
        }
        if system_instruction:
            body["system"] = system_instruction

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
            raise RuntimeError(f"Anthropic API error: {msg}")

        # Extract text from Anthropic message content
        parts = data.get("content") or []
        texts: list[str] = []
        for p in parts:
            if isinstance(p, dict) and p.get("type") == "text" and p.get("text"):
                texts.append(str(p.get("text")))
        if texts:
            return "".join(texts)

        # Fallbacks on some SDKs
        if isinstance(data.get("output_text"), str):
            return str(data["output_text"])

        raise RuntimeError("Claude completion: no text in response")

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
