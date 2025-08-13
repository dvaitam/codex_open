from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional


@dataclass
class RepoEntry:
    url: str
    last_used: float
    used_count: int


class RepoStore:
    def __init__(self, path: Path, max_items: int = 50):
        self.path = path
        self.max_items = max_items
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, RepoEntry]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
            out: Dict[str, RepoEntry] = {}
            for d in data.get("repos", []):
                url = d.get("url")
                if not url:
                    continue
                out[url] = RepoEntry(
                    url=url,
                    last_used=float(d.get("last_used", 0.0)),
                    used_count=int(d.get("used_count", 0)),
                )
            return out
        except Exception:
            return {}

    def _save(self, items: Dict[str, RepoEntry]) -> None:
        # Keep top-N by last_used
        sorted_items = sorted(items.values(), key=lambda r: r.last_used, reverse=True)[: self.max_items]
        payload = {"repos": [{"url": r.url, "last_used": r.last_used, "used_count": r.used_count} for r in sorted_items]}
        self.path.write_text(json.dumps(payload, indent=2))

    def list(self) -> List[RepoEntry]:
        items = self._load()
        return sorted(items.values(), key=lambda r: r.last_used, reverse=True)

    def add(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        items = self._load()
        now = time.time()
        if url in items:
            e = items[url]
            e.last_used = now
            e.used_count += 1
        else:
            items[url] = RepoEntry(url=url, last_used=now, used_count=1)
        self._save(items)

