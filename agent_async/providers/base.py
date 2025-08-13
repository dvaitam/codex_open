from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import AsyncIterator, Dict, List, Optional


Message = Dict[str, str]  # {role: system|user|assistant, content: str}


class Provider(abc.ABC):
    name: str

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key

    @abc.abstractmethod
    async def complete(self, model: str, messages: List[Message]) -> str:
        """Return a full string completion (no streaming)."""
        raise NotImplementedError

    async def list_models(self) -> List[str]:
        """Return available model ids for this provider.
        Default: empty (override per provider)."""
        return []


@dataclass
class SimpleProvider(Provider):
    name: str = "simple"

    def __init__(self, api_key: Optional[str] = None):
        super().__init__(api_key)

    async def complete(self, model: str, messages: List[Message]) -> str:
        # A minimal, deterministic provider for offline demo.
        last_user = next((m for m in reversed(messages) if m["role"] == "user"), {"content": ""})
        content = last_user.get("content", "")
        # Always propose a very safe command first.
        return (
            '{"type":"run","cmd":"git status --porcelain","thought":"Inspect workspace before changes."}'
        )

    async def list_models(self) -> List[str]:
        return [
            "local-simulate",
            "local-analyze",
            "local-refactor",
        ]
