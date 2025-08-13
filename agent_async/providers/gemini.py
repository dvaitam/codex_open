from __future__ import annotations

import os
from typing import List

import os
from .base import Message, Provider
from .util_http import http_get_json, http_post_json


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str | None = None):
        key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        super().__init__(key.strip() if key else None)

    async def complete(self, model: str, messages: List[Message]) -> str:
        if not self.api_key:
            raise RuntimeError("Gemini API key required for completion")
        if not model:
            # Attempt a sane default
            model = "gemini-1.5-pro"

        # Separate system messages for systemInstruction
        sys_msgs = [m["content"] for m in messages if m.get("role") == "system"]
        system_instruction = "\n\n".join(sys_msgs).strip() if sys_msgs else None

        # Map messages to Gemini content format
        contents = []
        role_map = {"user": "user", "assistant": "model"}
        for m in messages:
            r = m.get("role")
            if r == "system":
                continue
            rr = role_map.get(r, "user")
            contents.append({"role": rr, "parts": [{"text": m.get("content", "")} ]})

        def build_body(force_json: bool = True, extra_user_note: str | None = None):
            cons = list(contents)
            if extra_user_note:
                cons.append({"role": "user", "parts": [{"text": extra_user_note}]})
            cfg = {"temperature": 0.1}
            if force_json:
                cfg["responseMimeType"] = "application/json"
            b = {"contents": cons, "generationConfig": cfg}
            if system_instruction:
                b["systemInstruction"] = {"parts": [{"text": system_instruction}]}
            return b

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        debug_flag = bool(os.environ.get("AGENT_ASYNC_DEBUG_HTTP"))
        # First attempt: request JSON mime type
        data = await http_post_json(
            url,
            build_body(force_json=True),
            timeout=90,
            retries=2,
            backoff=1.8,
            debug=debug_flag,
        )
        # Parse candidate text
        candidates = data.get("candidates") or []
        texts_accum: list[str] = []
        for c in candidates:
            content = c.get("content") or {}
            parts = content.get("parts") or []
            # Some SDKs may return parts as list of dicts with different keys
            for p in parts:
                if not isinstance(p, dict):
                    continue
                if p.get("text"):
                    texts_accum.append(p["text"])
                # If the model returns a functionCall or other structured part, ignore here
        if texts_accum:
            return "".join(texts_accum)
        # Some responses might include top-level text or in candidate itself
        for c in candidates:
            if isinstance(c, dict) and c.get("text"):
                return str(c.get("text"))
        if "text" in data:
            return str(data["text"])
        # Surface error or promptFeedback
        if isinstance(data.get("error"), dict):
            msg = data["error"].get("message") or str(data["error"])[:200]
            raise RuntimeError(f"Gemini API error: {msg}")
        pf = data.get("promptFeedback")
        if pf:
            block = pf.get("blockReason") or pf.get("block_reason")
            if block:
                raise RuntimeError(f"Gemini prompt blocked: {block}")

        # Fallback retry: drop responseMimeType and add explicit instruction
        data2 = await http_post_json(
            url,
            build_body(
                force_json=False,
                extra_user_note="Respond with exactly one JSON object only; no markdown.",
            ),
            timeout=90,
            retries=2,
            backoff=1.8,
            debug=debug_flag,
        )
        candidates = data2.get("candidates") or []
        texts_accum = []
        for c in candidates:
            content = c.get("content") or {}
            parts = content.get("parts") or []
            for p in parts:
                if isinstance(p, dict) and p.get("text"):
                    texts_accum.append(p["text"])
        if texts_accum:
            return "".join(texts_accum)
        if isinstance(data2.get("error"), dict):
            msg = data2["error"].get("message") or str(data2["error"])[:200]
            raise RuntimeError(f"Gemini API error: {msg}")
        pf2 = data2.get("promptFeedback")
        if pf2:
            block = pf2.get("blockReason") or pf2.get("block_reason")
            if block:
                raise RuntimeError(f"Gemini prompt blocked: {block}")
        raise RuntimeError(
            f"Gemini completion: no text in response (keys: {', '.join(list(data2.keys())[:6])})"
        )

    async def list_models(self) -> list[str]:
        try:
            if not self.api_key:
                raise RuntimeError("Gemini API key required")
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self.api_key}"
            data = await http_get_json(url)
            arr = data.get("models") or []
            out = []
            for it in arr:
                if not isinstance(it, dict):
                    continue
                name = it.get("name") or it.get("id")
                methods = it.get("supportedGenerationMethods") or it.get("supported_generation_methods") or []
                if name and any(m in methods for m in ("generateContent", "generate_text", "generateText")):
                    out.append(name.split("/")[-1])
            if out:
                return out
            fallback = [x.get("name") for x in arr if isinstance(x, dict) and x.get("name")]
            if fallback:
                return fallback
        except Exception:
            pass
        return [
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.0-flash-exp",
        ]
