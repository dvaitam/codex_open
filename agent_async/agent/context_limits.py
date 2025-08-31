from __future__ import annotations

import os
from typing import Tuple, Optional


def _chars_per_token() -> int:
    try:
        v = int(os.environ.get("AGENT_ASYNC_CHARS_PER_TOKEN", "4"))
        return v if v > 0 else 4
    except Exception:
        return 4


def _hard_cap_chars() -> int:
    try:
        # Allow very large contexts by default but keep a reasonable ceiling
        return int(os.environ.get("AGENT_ASYNC_CONTEXT_HARD_CAP_CHARS", "2000000"))
    except Exception:
        return 2_000_000


def _default_per_message_max() -> int:
    try:
        return int(os.environ.get("AGENT_ASYNC_PER_MESSAGE_MAX_CHARS", "20000"))
    except Exception:
        return 20_000


def _guess_context_tokens(model: str) -> int:
    m = (model or "").lower()
    # Heuristic mapping; override via env for exact control as needed
    if not m:
        return 128_000
    if "gpt-5" in m:
        return 400_000
    if any(k in m for k in ("gpt-4o", "gpt-4.1", "o3", "o4")):
        return 128_000
    if "gpt-4" in m:
        return 128_000
    if "gpt-3.5" in m:
        return 16_384
    if any(k in m for k in ("claude-3.5", "claude-3-opus", "claude-3-sonnet", "sonnet")):
        return 200_000
    if any(k in m for k in ("haiku",)):
        return 200_000
    if any(k in m for k in ("gemini-1.5",)):
        return 1_000_000
    if any(k in m for k in ("deepseek",)):
        return 128_000
    if any(k in m for k in ("grok", "xai")):
        return 128_000
    return 128_000


def get_context_limits(model: Optional[str]) -> Tuple[int, int]:
    """Return (ctx_max_chars, per_message_max_chars) based on model.

    Env overrides:
    - AGENT_ASYNC_CONTEXT_MAX_CHARS: force a fixed ctx cap (chars)
    - AGENT_ASYNC_PER_MESSAGE_MAX_CHARS: force per-message cap (chars)
    - AGENT_ASYNC_CHARS_PER_TOKEN: conversion ratio (chars/token)
    - AGENT_ASYNC_CONTEXT_HARD_CAP_CHARS: absolute ceiling regardless of model
    """
    # Respect explicit fixed override if set
    env_ctx = os.environ.get("AGENT_ASYNC_CONTEXT_MAX_CHARS")
    if env_ctx:
        try:
            ctx_max = int(env_ctx)
        except Exception:
            ctx_max = 300_000
    else:
        tokens = _guess_context_tokens(model or "")
        ctx_max = tokens * _chars_per_token()
        ctx_max = min(ctx_max, _hard_cap_chars())

    # Per-message cap: explicit env wins, otherwise scale gently with ctx size
    env_per_msg = os.environ.get("AGENT_ASYNC_PER_MESSAGE_MAX_CHARS")
    if env_per_msg:
        try:
            per_msg = int(env_per_msg)
        except Exception:
            per_msg = 20_000
    else:
        # up to 10% of context but not exceeding a soft ceiling
        soft_cap = int(os.environ.get("AGENT_ASYNC_PER_MESSAGE_SOFT_CAP", "50000"))
        per_msg = min(max(10_000, ctx_max // 10), soft_cap)

    return max(10_000, ctx_max), max(5_000, per_msg)

