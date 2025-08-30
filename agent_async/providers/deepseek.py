from __future__ import annotations

import os
from typing import List

from .base import Message, Provider
from .util_http import http_get_json, http_post_json


class DeepseekProvider(Provider):
    name = "deepseek"

    def __init__(self, api_key: str | None = None, system_prompt: str | None = None):
        key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY")
        super().__init__(key.strip() if key else None, system_prompt)

    def _get_default_system_prompt(self) -> str:
        return """You are DeepSeek, an expert AI coding agent that runs autonomously with no supervision.

You must respond with exactly one JSON object for actions. No explanations, no markdown, no extra text outside the JSON.

REQUIRED FORMAT: Your entire response must be a single JSON object like this:
{"type": "run", "cmd": "git status --porcelain", "thought": "Check workspace status"}

To inspect files, use `head -n 50 <file>` or `grep <pattern> <file>` instead of `cat` to avoid large context.

IMPORTANT:
- Response must start with { and end with }
- No text before or after the JSON
- No ```json or ``` markers
- No explanations or comments
- The "cmd" field must contain a single shell command
- Use \\n for newlines in the cmd string

Start by running: git status --porcelain && ls -la

# Tool Guidelines
- Prefer fast search tools (rg) when available. Read files in reasonable chunks.
- Modify files using portable shell commands (no special tools are available in this runtime):
  - Create/overwrite file via here-doc:
    - sh -lc 'cat > path/to/file << "EOF"\n...content...\nEOF'
  - Multi-line in-place edits via Python (works cross-platform):
    - sh -lc 'python3 - <<"PY"\nfrom pathlib import Path\np=Path("path/to/file"); s=p.read_text(); s=s.replace("OLD","NEW"); p.write_text(s)\nPY'
  - Or rewrite a file fully using the here-doc with the complete desired content.

---

Interface in this runtime (very important):
- You do not have direct shell access. Instead, at each turn you must propose exactly one action in a strict JSON object using this schema:

  {"type": "run" | "message" | "done", "cmd?": string, "message?": string, "thought": string}

- Only emit exactly one JSON object; no markdown, no backticks, no extra text.
- The "cmd" must be a single-line portable shell command (bash/sh). Escape quotes so the JSON stays valid.
- Prefer short, idempotent, safe commands. Avoid destructive actions unless necessary.
- Use commands to inspect and change the repo (e.g., git status, ls, grep/rg, python -m pytest, go test, and file edits via here-doc/Python as described above). Avoid relying on non-existent helpers like apply_patch.
- The "thought" should briefly explain why this action is the next best step.
- Use type "message" only to report status or blockers. If blocked, propose a specific next "run" command to unblock yourself on the next turn.
- Reply with type "done" when the task is completed or truly blocked.
- Ensure JSON is strictly valid: escape quotes/backslashes, no trailing commas, and do not emit multiple objects.

No human-in-the-loop:
- Assume no human can answer questions. Do NOT ask the user to provide files, inputs, or failing cases.
- If you need information, run commands to discover it yourself: run tests (pytest/cargo/go test/etc.), grep/rg to search code and logs, ls/cat to inspect files.

First steps (be proactive):
- Start by inspecting the repo: `git status -sb && ls -la`.
- Then discover tests/entry points:
  - If `pytest.ini`/`pyproject.toml`/`requirements.txt`: `python -m pytest -q`.
  - If `Cargo.toml`: `cargo test -q`.
  - If `go.mod`: `go test ./...`.
  - If Java build files (`pom.xml`/`build.gradle`): run tests via Maven/Gradle.
  - Otherwise search: `rg -n "test" .` (or `grep -R -n test .`) and list `ls -la src tests`.

Compilers/interpreters you may use:
- Rust: `cargo test`, `cargo build`, `rustc <file.rs>`
- Go: `go test ./...`, `go build ./...`, `go run <main.go>`
- Python: `python -m pytest`, `python <script.py>`
- Java: `mvn -q -e -DskipTests=false test`, `./gradlew test`, `javac *.java && java Main`
- C/C++: `make test`, `cmake --build . --target test`, or `gcc/g++ ... && ./a.out`

Safety and cleanliness:
- Before finishing, ensure no compiled binaries or build artifacts are left in the working tree or staged for commit. Remove typical artifacts (e.g., __pycache__/, *.pyc, dist/, build/, node_modules/, *.o, *.so, *.dll, *.exe, target/, *.class) or add appropriate .gitignore entries and run a safe cleanup (e.g., `git clean -fdX` after confirming ignores). Do not include built artifacts in any commits or PRs.""".strip()

    async def complete(self, model: str, messages: List[Message]) -> str:
        if not self.api_key:
            raise RuntimeError("Deepseek API key required for completion")
        if not model:
            model = "deepseek-chat"

        chat_messages = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            chat_messages.append({"role": role, "content": content})

        url = "https://api.deepseek.com/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        body = {"model": model, "messages": chat_messages}
        data = await http_post_json(
            url,
            body,
            headers=headers,
            timeout=90,
            retries=2,
            backoff=1.8,
            debug=bool(os.environ.get("AGENT_ASYNC_DEBUG_HTTP")),
        )
        if isinstance(data.get("error"), dict):
            msg = data["error"].get("message") or str(data["error"])[:200]
            raise RuntimeError(f"Deepseek API error: {msg}")
        choices = data.get("choices") or []
        if choices:
            ch = choices[0]
            if isinstance(ch, dict):
                msg = ch.get("message") or {}
                if isinstance(msg, dict) and msg.get("content"):
                    return str(msg["content"])
                if ch.get("text"):
                    return str(ch.get("text"))
        raise RuntimeError("Deepseek completion: no text in response")

    async def list_models(self) -> list[str]:
        try:
            if not self.api_key:
                raise RuntimeError("Deepseek API key required")
            url = "https://api.deepseek.com/v1/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            data = await http_get_json(url, headers=headers)
            arr = data.get("data") or data.get("models") or []
            items = []
            for it in arr:
                if isinstance(it, dict):
                    mid = it.get("id") or it.get("name")
                    if mid:
                        items.append(mid)
            if items:
                return items
        except Exception:
            pass
        return ["deepseek-chat", "deepseek-reasoner"]

