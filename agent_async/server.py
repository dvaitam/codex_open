from __future__ import annotations

import argparse
import asyncio
import io
import json
import mimetypes
import os
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, parse_qs
import shlex
import shutil

from agent_async.core.run_registry import RunRegistry
from agent_async.core.events import EventBus
from agent_async.exec.local import LocalExecutor
from agent_async.providers.factory import provider_from_name
from agent_async.agent.loop import AgentRunner
from agent_async.core.repo_store import RepoStore


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
RUNS_DIR = Path.cwd() / "runs"
DATA_DIR = Path.cwd() / "data"
SSH_DIR = DATA_DIR / "ssh"
SSH_KEY_PATH = SSH_DIR / "id"


class RunManager:
    def __init__(self, runs_dir: Path):
        self.registry = RunRegistry(runs_dir)
        self._threads: Dict[str, threading.Thread] = {}
        self._api_keys: Dict[str, Optional[str]] = {}
        self._cancels: Dict[str, threading.Event] = {}

    def start(self, repo_path: Path, provider_name: str, model: Optional[str], task: str, api_key: Optional[str] = None, repo_url: Optional[str] = None, truncate_limit: Optional[int] = None) -> str:
        run = self.registry.create_run(repo_path, provider_name, model, task, repo_url=repo_url, truncate_limit=truncate_limit)
        if api_key:
            self._api_keys[run.id] = api_key
        self._cancels[run.id] = threading.Event()
        t = threading.Thread(target=self._worker, args=(run.id,), daemon=True)
        t.start()
        self._threads[run.id] = t
        return run.id

    def _worker(self, run_id: str) -> None:
        run = self.registry.get(run_id)
        event_bus = EventBus(run.events_path)
        api_key = self._api_keys.get(run.id)
        provider = provider_from_name(run.provider, api_key=api_key)

        async def _run():
            # If repo_url is provided and repo_path doesn't exist, clone it to workspace
            repo_path = Path(run.repo_path)
            event_bus.emit(
                "agent.message",
                {
                    "role": "info",
                    "content": f"Starting run with provider={run.provider}, model={run.model or ''}, api_key_present={'yes' if bool(self._api_keys.get(run.id)) else 'no'}",
                },
            )
            if run.repo_url and not repo_path.exists():
                workspace = repo_path.parent
                workspace.mkdir(parents=True, exist_ok=True)
                src = shlex.quote(run.repo_url)
                dst = shlex.quote(str(repo_path))
                # If SSH key is present, use it for clone
                ssh_prefix = ""
                try:
                    key_path = SSH_KEY_PATH  # may not exist in older builds
                except NameError:
                    key_path = None
                if key_path and Path(key_path).exists():
                    ssh_prefix = f"GIT_SSH_COMMAND='ssh -i {shlex.quote(str(key_path))} -o StrictHostKeyChecking=no' "
                clone_cmd = f"{ssh_prefix}git clone {src} {dst}"
                event_bus.emit("agent.message", {"role": "thought", "content": f"Cloning repository: {run.repo_url}"})
                event_bus.emit("agent.command", {"cmd": clone_cmd})
                # Use a temporary executor with workspace cwd
                temp_exec = LocalExecutor(cwd=workspace)
                async for stream, text in temp_exec.run(clone_cmd):
                    if stream == "stdout":
                        event_bus.emit("proc.stdout", {"text": text})
                    else:
                        event_bus.emit("proc.stderr", {"text": text})
                # Validate clone
                if not repo_path.exists() or not (repo_path / ".git").exists():
                    event_bus.emit("agent.error", {"error": "git clone failed or repository not present"})
                    event_bus.emit("agent.done", {})
                    return

            executor = LocalExecutor(cwd=Path(run.repo_path))
            # Compose a dynamic cancel_check that reads the current event each time
            def cancel_check():
                evt = self._cancels.get(run.id)
                return bool(evt and evt.is_set())
            runner = AgentRunner(event_bus=event_bus, provider=provider, executor=executor, truncate_limit=run.truncate_limit, cancel_check=cancel_check)
            await runner.run(run_id=run.id, task=run.task, model=run.model)

        try:
            asyncio.run(_run())
        except asyncio.CancelledError:
            # Graceful cancel: mark as done
            try:
                EventBus(run.events_path).emit("agent.message", {"role": "info", "content": "Run cancelled."})
                EventBus(run.events_path).emit("agent.done", {})
            except Exception:
                pass
        except Exception as e:
            event_bus.emit("agent.error", {"error": str(e)})


MANAGER = RunManager(RUNS_DIR)
REPOS = RepoStore(DATA_DIR / "repos.json")


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict):
    data = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentAsyncHTTP/0.1"

    def log_message(self, format, *args):
        # Quiet high-frequency event polling unless explicitly enabled
        quiet = os.environ.get("AGENT_ASYNC_QUIET_EVENTS", "1") not in ("0", "false", "no")
        try:
            path = self.path
        except Exception:
            path = ""
        if quiet and path.startswith("/api/run/") and path.endswith("/events"):
            return
        return super().log_message(format, *args)

    def do_OPTIONS(self):  # CORS preflight (not strictly needed for same-origin)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/runs"):
            return self._api_runs()
        if path.startswith("/api/models"):
            return self._api_models()
        if path == "/api/repos":
            return self._api_repos_list()
        if path == "/api/ssh-key":
            return self._api_ssh_key_get()
        if path.startswith("/api/run/") and path.endswith("/events"):
            return self._api_run_events()
        if path.startswith("/api/run/"):
            return self._api_run_meta()
        return self._serve_static(path)

    def do_POST(self):
        if self.path == "/api/run":
            return self._api_run_create()
        if self.path == "/api/repos":
            return self._api_repos_add()
        if self.path.startswith("/api/run/") and self.path.endswith("/pr"):
            return self._api_run_create_pr()
        if self.path.startswith("/api/run/") and self.path.endswith("/cancel"):
            return self._api_run_cancel()
        if self.path == "/api/ssh-key":
            return self._api_ssh_key_save()
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        if self.path == "/api/ssh-key":
            return self._api_ssh_key_delete()
        if self.path.startswith("/api/run/"):
            return self._api_run_delete()
        self.send_error(HTTPStatus.NOT_FOUND)

    # --- Static assets ---
    def _serve_static(self, path: str):
        p = path
        if p == "/":
            p = "/index.html"
        # prevent path traversal
        safe = os.path.normpath(p).lstrip("/")
        file_path = (WEB_DIR / safe).resolve()
        if not str(file_path).startswith(str(WEB_DIR)) or not file_path.exists() or file_path.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype, _ = mimetypes.guess_type(str(file_path))
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- API ---
    def _api_run_create(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body or b"{}")
        except Exception:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})

        repo_url = payload.get("repo_url") or payload.get("repo")
        provider = payload.get("provider", "simple")
        model = payload.get("model")
        task = payload.get("task")
        api_key_raw = payload.get("api_key")
        api_key = api_key_raw.strip() if isinstance(api_key_raw, str) else None
        # Truncation options: by default do not truncate (send full output)
        truncate_flag = payload.get("truncate")
        truncate_limit = payload.get("truncate_limit")
        trunc_limit_val: Optional[int]
        if isinstance(truncate_flag, bool) and truncate_flag:
            try:
                trunc_limit_val = int(truncate_limit) if truncate_limit is not None else 4000
            except Exception:
                trunc_limit_val = 4000
        else:
            trunc_limit_val = None
        if not repo_url or not task:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Missing repo_url or task"})

        # Determine if input is a URL (clone) or a local path
        is_url = any(repo_url.startswith(p) for p in ("http://", "https://", "git@", "ssh://"))
        workspace = Path.cwd() / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        target_path: Path
        clone_url: Optional[str] = None
        if is_url:
            # Derive folder name from URL and run id to avoid collisions
            name = repo_url.rstrip("/").split("/")[-1]
            if name.endswith(".git"):
                name = name[:-4]
            # Create a temporary Run ID to compute path consistently? We'll use a placeholder; actual run id will be added in manager.
            # Instead, create a unique folder with timestamp
            ts = time.strftime('%Y%m%d-%H%M%S')
            uid = ts
            folder = f"{name}-{uid}-{uuid.uuid4().hex[:6]}"
            target_path = workspace / folder
            clone_url = repo_url
        else:
            p = Path(repo_url).expanduser().resolve()
            if not p.exists() or not p.is_dir():
                return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Repo path not found"})
            target_path = p

        try:
            run_id = MANAGER.start(target_path, provider, model, task, api_key, repo_url=clone_url, truncate_limit=trunc_limit_val)
            # Save repo URL or local path to recent list
            REPOS.add(repo_url)
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

        return _json_response(self, HTTPStatus.OK, {"run_id": run_id})

    def _api_runs(self):
        # list run ids and meta
        items = []
        for run_dir in sorted(RUNS_DIR.glob("*")):
            meta_path = run_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text())
                    items.append(meta)
                except Exception:
                    pass
        return _json_response(self, HTTPStatus.OK, {"runs": items})

    def _parse_run_id(self) -> Optional[str]:
        parts = urlparse(self.path).path.split("/")
        try:
            idx = parts.index("run")
            return parts[idx + 1]
        except Exception:
            return None

    def _api_run_meta(self):
        run_id = self._parse_run_id()
        if not run_id:
            return self.send_error(HTTPStatus.NOT_FOUND)
        meta_path = RUNS_DIR / run_id / "meta.json"
        if not meta_path.exists():
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            meta = json.loads(meta_path.read_text())
        except Exception:
            return self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
        return _json_response(self, HTTPStatus.OK, meta)

    def _api_run_events(self):
        # long-poll style incremental fetch using byte position
        run_id = self._parse_run_id()
        if not run_id:
            return self.send_error(HTTPStatus.NOT_FOUND)
        events_path = RUNS_DIR / run_id / "events.jsonl"
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        try:
            pos = int((q.get("pos", ["0"])[0]))
        except ValueError:
            pos = 0
        limit = int((q.get("limit", ["100"])[0]))

        next_pos = pos
        events = []
        if events_path.exists():
            with events_path.open("rb") as f:
                size = f.seek(0, io.SEEK_END)
                if pos > size:
                    pos = size
                f.seek(pos)
                for _ in range(limit):
                    line = f.readline()
                    if not line:
                        break
                    next_pos = f.tell()
                    try:
                        events.append(json.loads(line.decode("utf-8")))
                    except Exception:
                        pass
        return _json_response(self, HTTPStatus.OK, {"next_pos": next_pos, "events": events})

    def _api_repos_list(self):
        try:
            items = REPOS.list()
            return _json_response(self, HTTPStatus.OK, {"repos": [
                {"url": r.url, "last_used": r.last_used, "used_count": r.used_count} for r in items
            ]})
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e), "repos": []})

    def _api_repos_add(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body or b"{}")
        except Exception:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
        url = (payload.get("repo_url") or payload.get("url") or "").strip()
        if not url:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "repo_url required"})
        try:
            REPOS.add(url)
            return _json_response(self, HTTPStatus.OK, {"ok": True})
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    # --- SSH key management ---
    def _api_ssh_key_get(self):
        try:
            present = SSH_KEY_PATH.exists() and SSH_KEY_PATH.is_file()
            return _json_response(self, HTTPStatus.OK, {"present": bool(present)})
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    def _api_ssh_key_save(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body or b"{}")
        except Exception:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})
        private_key = (payload.get("private_key") or "").strip()
        if not private_key:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "private_key required"})
        try:
            SSH_DIR.mkdir(parents=True, exist_ok=True)
            SSH_KEY_PATH.write_text(private_key if private_key.endswith("\n") else private_key + "\n")
            os.chmod(SSH_KEY_PATH, 0o600)
            return _json_response(self, HTTPStatus.OK, {"ok": True})
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    def _api_ssh_key_delete(self):
        try:
            if SSH_KEY_PATH.exists():
                SSH_KEY_PATH.unlink()
            return _json_response(self, HTTPStatus.OK, {"ok": True})
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    def _api_models(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        provider = (q.get("provider", [""])[0]).lower()
        api_key = q.get("api_key", [None])[0]
        debug_q = q.get("debug", ["0"])[0]
        if not provider:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "provider required"})
        fallback_map = {
            "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini"],
            "gemini": ["gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash-exp"],
            "claude": ["claude-3-5-sonnet-latest", "claude-3-opus-latest", "claude-3-5-haiku-latest"],
            "xai": ["grok-2-latest", "grok-2-mini", "grok-beta"],
            "simple": ["local-simulate", "local-analyze", "local-refactor"],
        }
        try:
            # Enable HTTP debug for this request if asked
            prev_debug = os.environ.get("AGENT_ASYNC_DEBUG_HTTP")
            if debug_q in ("1", "true", "yes"):
                os.environ["AGENT_ASYNC_DEBUG_HTTP"] = "1"
            prov = provider_from_name(provider, api_key=api_key)
            models = asyncio.run(prov.list_models())
            if not models:
                return _json_response(self, HTTPStatus.OK, {"models": fallback_map.get(provider, [])})
            return _json_response(self, HTTPStatus.OK, {"models": models})
        except Exception as e:
            return _json_response(self, HTTPStatus.OK, {"error": str(e), "models": fallback_map.get(provider, [])})
        finally:
            if debug_q in ("1", "true", "yes"):
                if prev_debug is None:
                    os.environ.pop("AGENT_ASYNC_DEBUG_HTTP", None)
                else:
                    os.environ["AGENT_ASYNC_DEBUG_HTTP"] = prev_debug

    def _api_run_create_pr(self):
        run_id = self._parse_run_id()
        if not run_id:
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            payload = json.loads(body or b"{}")
        except Exception:
            return _json_response(self, HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON"})

        branch = (payload.get("branch") or "").strip()
        title = (payload.get("title") or "").strip()
        pr_body = (payload.get("body") or "").strip()

        # Emit immediate feedback into the run stream
        try:
            reg = RunRegistry(base_dir=RUNS_DIR)
            run = reg.get(run_id)
            EventBus(run.events_path).emit("agent.message", {"role": "info", "content": f"PR requested: branch='{branch or '(auto)'}' title='{title or '(auto)'}'"})
        except Exception:
            pass

        # Launch background PR worker streaming to events
        t = threading.Thread(target=_create_pr_worker, args=(run_id, branch, title, pr_body), daemon=True)
        t.start()
        return _json_response(self, HTTPStatus.OK, {"ok": True})

    def _api_run_cancel(self):
        run_id = self._parse_run_id()
        if not run_id:
            return self.send_error(HTTPStatus.NOT_FOUND)
        try:
            evt = MANAGER._cancels.get(run_id)
            if not evt:
                # Lazily create a cancel event for older runs
                MANAGER._cancels[run_id] = threading.Event()
                evt = MANAGER._cancels[run_id]
            evt.set()
            run = MANAGER.registry.get(run_id)
            eb = EventBus(run.events_path)
            eb.emit("agent.message", {"role": "info", "content": "Cancellation requested by user."})
            eb.emit("agent.done", {})
            return _json_response(self, HTTPStatus.OK, {"ok": True})
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})

    def _api_run_delete(self):
        run_id = self._parse_run_id()
        if not run_id:
            return self.send_error(HTTPStatus.NOT_FOUND)
        removed_run = False
        removed_repo = False
        skip_reason = None
        try:
            # Cancel if running
            evt = MANAGER._cancels.get(run_id)
            if not evt:
                MANAGER._cancels[run_id] = threading.Event()
                evt = MANAGER._cancels[run_id]
            evt.set()

            run = MANAGER.registry.get(run_id)

            # Decide if we can remove repo directory safely
            repo_path = Path(run.repo_path).resolve()
            workspace = (Path.cwd() / "workspace").resolve()
            # Check if any other run references the same repo_path
            referenced_elsewhere = False
            for other_dir in RUNS_DIR.glob("*"):
                if other_dir.name == run_id:
                    continue
                meta_path = other_dir / "meta.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = json.loads(meta_path.read_text())
                except Exception:
                    continue
                if Path(meta.get("repo_path", "")).resolve() == repo_path:
                    referenced_elsewhere = True
                    break

            # Only delete repo if it was cloned (repo_url present), inside workspace, exists, and not referenced
            if run.repo_url and repo_path.exists():
                try:
                    inside_workspace = False
                    try:
                        inside_workspace = repo_path.is_relative_to(workspace)  # py3.9+: but available in 3.11
                    except AttributeError:
                        inside_workspace = str(repo_path).startswith(str(workspace) + os.sep)
                    if inside_workspace and not referenced_elsewhere:
                        shutil.rmtree(repo_path, ignore_errors=True)
                        removed_repo = True
                    else:
                        skip_reason = "repo outside workspace or referenced by another run"
                except Exception as e:
                    skip_reason = f"repo delete error: {e}"

            # Remove run directory
            try:
                shutil.rmtree(RUNS_DIR / run_id, ignore_errors=True)
                removed_run = True
            except Exception:
                pass

            # Cleanup manager state
            MANAGER._api_keys.pop(run_id, None)
            MANAGER._cancels.pop(run_id, None)
            return _json_response(self, HTTPStatus.OK, {"ok": True, "removed_run": removed_run, "removed_repo": removed_repo, "skip_reason": skip_reason})
        except Exception as e:
            return _json_response(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(e)})


def _norm_branch_name(name: str) -> str:
    safe = []
    for ch in name.lower():
        if ch.isalnum() or ch in ("-", "_"):
            safe.append(ch)
        elif ch.isspace() or ch in ("/", "\\", ":"):
            safe.append("-")
    out = "".join(safe).strip("-")
    return out[:60] or "changes"


def _create_pr_worker(run_id: str, branch: str, title: str, pr_body: str) -> None:
    registry = RunRegistry(base_dir=RUNS_DIR)
    run = registry.get(run_id)
    event_bus = EventBus(run.events_path)
    repo = Path(run.repo_path)
    ex = LocalExecutor(cwd=repo)

    async def _run():
        # Derive defaults
        br = branch or f"feat/{_norm_branch_name(run.task or 'change')}"
        ttl = title or f"Agent: {run.task}"
        body = pr_body or f"Automated PR for run {run.id}\n\nTask: {run.task}"

        # Ensure git repo exists
        event_bus.emit("agent.message", {"role": "info", "content": f"Preparing PR on branch {br}"})

        async def run_cmd(cmd: str):
            event_bus.emit("agent.command", {"cmd": cmd})
            async for stream, text in ex.run(cmd):
                event_bus.emit("proc.stdout" if stream == "stdout" else "proc.stderr", {"text": text})

        # Detect default branch
        # If SSH key exists, prefix git commands to use it
        ssh_prefix = ""
        try:
            key_path = SSH_KEY_PATH
        except NameError:
            key_path = None
        if key_path and Path(key_path).exists():
            ssh_prefix = f"GIT_SSH_COMMAND='ssh -i {shlex.quote(str(key_path))} -o StrictHostKeyChecking=no' "

        await run_cmd(f"{ssh_prefix}git remote -v")
        await run_cmd(f"{ssh_prefix}git fetch --all --prune")
        # Try to compute base branch
        base_branch = "main"
        # symbolic-ref might fail if not set
        await run_cmd("git symbolic-ref --quiet refs/remotes/origin/HEAD || true")
        # Create or switch branch
        await run_cmd(f"git checkout -B {br}")
        await run_cmd("git add -A")
        # Commit if there are staged changes
        await run_cmd("git diff --cached --quiet || git commit -m \"" + ttl.replace("\"", "\\\"") + "\"")

        # Try with GitHub CLI if available
        await run_cmd("which gh || command -v gh || echo 'gh not found'")
        # Push branch (with SSH if available)
        await run_cmd(f"{ssh_prefix}git push -u origin {br}")
        # Create PR via gh if available; otherwise print compare URL
        await run_cmd(
            "if command -v gh >/dev/null 2>&1; then "
            + "gh pr create -t '" + ttl.replace("'", "'\''") + "' -b '" + body.replace("'", "'\''") + "' -H '" + br + "' || true; "
            + "else echo 'gh not installed; open your repository and create a PR from branch "
            + br + "'; fi"
        )
        event_bus.emit("agent.message", {"role": "info", "content": "PR step finished (check output for URL or errors)."})

    try:
        asyncio.run(_run())
    except Exception as e:
        event_bus.emit("agent.error", {"error": str(e)})


def serve(host: str = "127.0.0.1", port: int = 8765):
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving UI at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Agent Async Web Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
