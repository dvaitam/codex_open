import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List


class EventBus:
    def __init__(self, jsonl_path: Path):
        self.path = jsonl_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sinks: List[Callable[[Dict[str, Any]], None]] = []

    def subscribe(self, sink: Callable[[Dict[str, Any]], None]) -> None:
        self._sinks.append(sink)

    def emit(self, type: str, data: Dict[str, Any]) -> None:
        evt = {"ts": time.time(), "type": type, "data": data}
        # append to jsonl
        with self.path.open("a") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")
        for s in list(self._sinks):
            try:
                s(evt)
            except Exception:
                pass


class ConsolePrinter:
    def handle(self, evt: Dict[str, Any]) -> None:
        t = evt.get("type")
        d = evt.get("data", {})
        if t == "agent.command":
            cmd = d.get("cmd", "")
            print(f"\n$ {cmd}")
        elif t == "proc.stdout":
            text = d.get("text", "")
            if text:
                sys.stdout.write(text)
                sys.stdout.flush()
        elif t == "proc.stderr":
            text = d.get("text", "")
            if text:
                sys.stderr.write(text)
                sys.stderr.flush()
        elif t == "agent.message":
            role = d.get("role", "agent")
            content = d.get("content", "")
            print(f"\n[{role}] {content}")
        elif t == "agent.error":
            print(f"\n[error] {d.get('error')}")
        elif t == "agent.done":
            print("\n[done] agent completed")
        elif t == "provider.reply":
            path = d.get("file", "")
            b = d.get("bytes")
            print(f"\n[provider.reply] {b} bytes saved to {path}")
        elif t == "provider.start":
            prov = d.get("provider", "")
            model = d.get("model", "")
            msgs = d.get("messages")
            extra = f" msgs={msgs}" if msgs is not None else ""
            print(f"\n[provider.start] {prov} {model}{extra}")
        elif t == "provider.end":
            prov = d.get("provider", "")
            model = d.get("model", "")
            ok = d.get("ok")
            dur = d.get("duration_ms")
            err = d.get("error")
            canceled = d.get("cancelled")
            parts = [f"[provider.end] {prov} {model}"]
            if ok is not None:
                parts.append(f"ok={ok}")
            if dur is not None:
                parts.append(f"{dur}ms")
            if canceled:
                parts.append("cancelled")
            if err:
                parts.append(f"error={err}")
            print("\n" + " ".join(parts))
