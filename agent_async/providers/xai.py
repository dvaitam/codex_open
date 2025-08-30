from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json, http_post_json


class XAIProvider(Provider):
    name = "xai"

    def __init__(self, api_key: str | None = None, system_prompt: str | None = None):
        key = api_key if api_key is not None else os.environ.get("XAI_API_KEY")
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
            raise RuntimeError("xAI API key required for completion")
        if not model:
            model = "grok-beta"
        
        debug_flag = bool(os.environ.get("AGENT_ASYNC_DEBUG_HTTP"))

        # Use only the specified model, no fallbacks
        models_to_try = [model]
        
        last_error = None
        for attempt_model in models_to_try:
            try:
                return await self._complete_with_model(attempt_model, messages, debug_flag)
            except Exception as e:
                last_error = e
                if debug_flag:
                    print(f"DEBUG xAI model {attempt_model} failed: {e}")
                continue
        
        raise last_error or RuntimeError("All xAI models failed")

    async def _complete_with_model(self, model: str, messages: List[Message], debug_flag: bool) -> str:
        chat_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            
            # xAI might not handle system role properly, convert to user message
            if role == "system":
                chat_messages.append({"role": "user", "content": f"SYSTEM: {content}\n\nIMPORTANT: Respond with exactly one JSON object only. No extra text."})
            else:
                chat_messages.append({"role": role, "content": content})

        url = "https://api.x.ai/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = {"model": model, "messages": chat_messages, "max_tokens": 8000}
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
                # Try different possible response formats
                msg = choice.get("message") or {}
                if isinstance(msg, dict) and msg.get("content"):
                    content = str(msg["content"]).strip()
                    # Strip markdown code blocks if present
                    if content.startswith("```") and content.endswith("```"):
                        content = content[3:-3].strip()
                        if content.startswith("json"):
                            content = content[4:].strip()
                    if debug_flag:
                        print(f"DEBUG xAI response: {content[:200]}...")
                    return content
                if choice.get("text"):
                    content = str(choice.get("text")).strip()
                    # Strip markdown code blocks if present
                    if content.startswith("```") and content.endswith("```"):
                        content = content[3:-3].strip()
                        if content.startswith("json"):
                            content = content[4:].strip()
                    if debug_flag:
                        print(f"DEBUG xAI response: {content[:200]}...")
                    return content
                # Some APIs return content directly in the choice
                if choice.get("content"):
                    content = str(choice["content"]).strip()
                    # Strip markdown code blocks if present
                    if content.startswith("```") and content.endswith("```"):
                        content = content[3:-3].strip()
                        if content.startswith("json"):
                            content = content[4:].strip()
                    if debug_flag:
                        print(f"DEBUG xAI response: {content[:200]}...")
                    return content
        
        # Check if the response is directly in the data object
        if data.get("content"):
            content = str(data["content"]).strip()
            # Strip markdown code blocks if present
            if content.startswith("```") and content.endswith("```"):
                content = content[3:-3].strip()
                if content.startswith("json"):
                    content = content[4:].strip()
            if debug_flag:
                print(f"DEBUG xAI direct content: {content[:200]}...")
            return content
        if data.get("text"):
            content = str(data["text"]).strip()
            # Strip markdown code blocks if present
            if content.startswith("```") and content.endswith("```"):
                content = content[3:-3].strip()
                if content.startswith("json"):
                    content = content[4:].strip()
            if debug_flag:
                print(f"DEBUG xAI direct text: {content[:200]}...")
            return content
        
        # Debug: print the full response if no valid content found
        if debug_flag:
            print(f"DEBUG xAI full response: {data}")
        
        raise RuntimeError(f"xAI completion: no text in response for model {model}")

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
