import argparse
import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

from agent_async.core.events import ConsolePrinter, EventBus
from agent_async.core.run_registry import RunRegistry
from agent_async.exec.local import LocalExecutor
from agent_async.agent.loop import AgentRunner
from agent_async.providers.factory import provider_from_name


def _ensure_repo(path: str) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        raise SystemExit(f"Repo path not found or not a directory: {p}")
    return p


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-async", description="Async coding agent CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    start = sub.add_parser("start", help="Start a task and stream output")
    start.add_argument("--repo", required=True, help="Path to local repo")
    start.add_argument("--provider", default="simple", help="Provider: openai|claude|gemini|xai|deepseek|simple")
    start.add_argument("--model", default=None, help="Model name, provider-specific")
    start.add_argument("--task", required=True, help="Programming task description")
    start.add_argument("--detached", action="store_true", help="Run in background and return run id")
    start.add_argument("--system-prompt", default=None, help="Custom system prompt to use (optional)")
    start.add_argument("--debug", action="store_true", help="Enable debug mode for HTTP requests")

    watch = sub.add_parser("watch", help="Watch an existing run's events")
    watch.add_argument("--run", required=True, help="Run id to watch")

    worker = sub.add_parser("worker", help="Internal: run a task in worker mode")
    worker.add_argument("--run_dir", required=True, help="Run directory path")

    return parser


async def start_run(args: argparse.Namespace) -> int:
    repo_path = _ensure_repo(args.repo)

    registry = RunRegistry(base_dir=Path.cwd() / "runs")
    run = registry.create_run(repo_path=repo_path, provider=args.provider, model=args.model, task=args.task, system_prompt=args.system_prompt)

    if args.detached:
        # Spawn a background worker process to execute this run
        log_file = open(run.dir / "worker.log", "a")
        cmd = [sys.executable, "-m", "agent_async.cli", "worker", "--run_dir", str(run.dir)]
        # Detach cross-platform-ish
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x00000008  # CREATE_NEW_CONSOLE
        proc = None
        try:
            proc = asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
            )
        except TypeError:
            # Fallback to sync Popen if loop not started yet for detached
            import subprocess

            subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
        print(run.id)
        return 0

    # Foreground streaming execution
    event_bus = EventBus(run.events_path)
    printer = ConsolePrinter()
    event_bus.subscribe(printer.handle)

    provider = provider_from_name(args.provider, system_prompt=args.system_prompt)
    event_bus.emit("agent.message", {"role": "info", "content": f"System prompt:\n---\n{provider.system_prompt}\n---"})
    executor = LocalExecutor(cwd=repo_path)
    runner = AgentRunner(event_bus=event_bus, provider=provider, executor=executor)

    try:
        await runner.run(run_id=run.id, task=args.task, model=args.model)
    except KeyboardInterrupt:
        event_bus.emit(type="agent.error", data={"error": "Interrupted"})
        return 130
    return 0


async def worker_mode(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    registry = RunRegistry(base_dir=Path.cwd() / "runs")
    run = registry.open_run_dir(run_dir)

    event_bus = EventBus(run.events_path)
    provider = provider_from_name(run.provider, system_prompt=run.system_prompt)
    event_bus.emit("agent.message", {"role": "info", "content": f"System prompt:\n---\n{provider.system_prompt}\n---"})
    executor = LocalExecutor(cwd=Path(run.repo_path))
    runner = AgentRunner(event_bus=event_bus, provider=provider, executor=executor)
    try:
        await runner.run(run_id=run.id, task=run.task, model=run.model)
    except Exception as e:
        event_bus.emit(type="agent.error", data={"error": str(e)})
        return 1
    return 0


async def watch_run(args: argparse.Namespace) -> int:
    registry = RunRegistry(base_dir=Path.cwd() / "runs")
    run = registry.get(args.run)
    printer = ConsolePrinter()

    # Simple polling tail of the JSONL file
    events_path = run.events_path
    last_size = 0
    try:
        while True:
            if events_path.exists():
                size = events_path.stat().st_size
                if size > last_size:
                    with events_path.open("r") as f:
                        f.seek(last_size)
                        for line in f:
                            try:
                                evt = json.loads(line)
                                printer.handle(evt)
                            except Exception:
                                pass
                    last_size = size
            await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        return 0


def main(argv=None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    if args.cmd == "start":
        return asyncio.run(start_run(args))
    if args.cmd == "watch":
        return asyncio.run(watch_run(args))
    if args.cmd == "worker":
        return asyncio.run(worker_mode(args))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
