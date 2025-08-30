import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Run:
    id: str
    dir: Path
    repo_path: str
    repo_url: Optional[str]
    provider: str
    model: Optional[str]
    task: str
    system_prompt: Optional[str] = None
    truncate_limit: Optional[int] = None

    @property
    def events_path(self) -> Path:
        return self.dir / "events.jsonl"


class RunRegistry:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_run(self, repo_path: Path, provider: str, model: Optional[str], task: str, repo_url: Optional[str] = None, system_prompt: Optional[str] = None, truncate_limit: Optional[int] = None) -> Run:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        run_dir = self.base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "id": run_id,
            "repo_path": str(repo_path),
            "repo_url": repo_url,
            "provider": provider,
            "model": model,
            "task": task,
            "system_prompt": system_prompt,
            "truncate_limit": truncate_limit,
        }
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return Run(id=run_id, dir=run_dir, repo_path=str(repo_path), repo_url=repo_url, provider=provider, model=model, task=task, system_prompt=system_prompt, truncate_limit=truncate_limit)

    def get(self, run_id: str) -> Run:
        run_dir = self.base_dir / run_id
        return self.open_run_dir(run_dir)

    def open_run_dir(self, run_dir: Path) -> Run:
        meta = json.loads((run_dir / "meta.json").read_text())
        return Run(
            id=meta["id"],
            dir=run_dir,
            repo_path=meta["repo_path"],
            repo_url=meta.get("repo_url"),
            provider=meta["provider"],
            model=meta.get("model"),
            task=meta["task"],
            system_prompt=meta.get("system_prompt"),
            truncate_limit=meta.get("truncate_limit"),
        )
