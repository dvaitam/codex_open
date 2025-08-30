from __future__ import annotations

from typing import Dict

from .base import Provider, SimpleProvider
from .openai import OpenAIProvider
from .gemini import GeminiProvider
from .xai import XAIProvider
from .claude import ClaudeProvider
from .deepseek import DeepseekProvider


def provider_from_name(name: str, api_key: str | None = None, system_prompt: str | None = None) -> Provider:
    n = (name or "").lower()
    if n in ("simple", "mock"):
        return SimpleProvider(api_key, system_prompt)
    if n == "openai":
        return OpenAIProvider(api_key, system_prompt)
    if n == "gemini":
        return GeminiProvider(api_key, system_prompt)
    if n == "xai":
        return XAIProvider(api_key, system_prompt)
    if n == "claude":
        return ClaudeProvider(api_key, system_prompt)
    if n == "deepseek":
        return DeepseekProvider(api_key, system_prompt)
    raise ValueError(f"Unknown provider: {name}")
