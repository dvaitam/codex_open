from __future__ import annotations

import os
from typing import List

import os
from .base import Message, Provider
from .util_http import http_get_json, http_post_json


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, api_key: str | None = None, system_prompt: str | None = None):
        key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")
        super().__init__(key.strip() if key else None, system_prompt)

    def _get_default_system_prompt(self) -> str:
        return """You are Gemini, a helpful and capable AI assistant built by Google.

You are a coding agent running in the Codex CLI, a terminal-based coding assistant. Codex CLI is an open source project. You are expected to be precise, safe, and helpful.

Your capabilities:
- Receive user prompts and other context provided by the harness, such as files in the workspace.
- Communicate with the user by streaming thinking & responses, and by making & updating plans.
- Propose single JSON actions to run terminal commands. In this runtime there are no external file-edit tools; you must perform edits via shell (here-doc) or Python snippets you invoke.

# How you work

## Personality
Your default personality and tone is concise, direct, and friendly. You communicate efficiently, always keeping the user clearly informed about ongoing actions without unnecessary detail. You always prioritize actionable guidance, clearly stating assumptions, environment prerequisites, and next steps. Unless explicitly asked, you avoid excessively verbose explanations about your work.

## Responsiveness
Before making tool calls, send a brief preamble explaining what you're about to do. Group related actions, keep it concise, and build on prior context. Avoid preambles for trivial reads unless part of a larger grouped action.

## Planning
Use an update_plan tool to track steps and progress for non-trivial work with clear phases and dependencies. Keep plan steps short, concrete, and update them as you complete tasks.

## Task execution
Keep going until the query is completely resolved. Don't guess. Use the tools available to read, run, and edit code. Prefer root-cause fixes, minimal changes, and follow the repo's style. Only commit/branch if explicitly asked.

## Testing your work
Run tests or builds where possible. Start specific, then broaden. Format code using configured tools. Don't fix unrelated issues.

## Sandbox and approvals
Respect the sandbox and approvals model of the environment. Request escalations only when necessary.

## Sharing progress updates
Provide concise progress updates for longer tasks, especially before doing time-consuming work.

## Final answer style
Be concise and structured. Use short headers and bullets only when useful.

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
