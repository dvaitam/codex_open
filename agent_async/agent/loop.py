import asyncio
import json
import re
import time
import os
from typing import List, Optional, Callable

from agent_async.core.events import EventBus
from agent_async.exec.local import LocalExecutor
from agent_async.providers.base import Message, Provider
from .prompt import SYSTEM_PROMPT


class AgentRunner:
    def __init__(self, event_bus: EventBus, provider: Provider, executor: LocalExecutor, truncate_limit: Optional[int] = None, cancel_check: Optional[Callable[[], bool]] = None):
        self.bus = event_bus
        self.provider = provider
        self.executor = executor
        self.truncate_limit = truncate_limit
        self.cancel_check = cancel_check

    async def run(self, run_id: str, task: str, model: Optional[str]) -> None:
        original_transcript: List[Message] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}"},
        ]
        transcript: List[Message] = list(original_transcript)
        # Bootstrap: nudge model to propose a first 'run' action to inspect the repo
        transcript.append({
            "role": "user",
            "content": (
                "Propose a 'run' action now to inspect the repo, e.g., git status -sb && ls -la."
            ),
        })

        max_steps = 50
        invalid_count = 0
        consecutive_message_only = 0
        no_text_resets = 0
        think_timeout = int(os.environ.get("AGENT_ASYNC_THINK_TIMEOUT", "120"))
        for step in range(max_steps):
            # Check for cancellation
            if self.cancel_check and self.cancel_check():
                self.bus.emit("agent.message", {"role": "info", "content": "Run cancelled by user."})
                self.bus.emit("agent.done", {})
                break
            try:
                # Emit provider.start so users can correlate waiting periods
                prov_name = getattr(self.provider, "name", "")
                self.bus.emit("provider.start", {"provider": prov_name, "model": model or "", "messages": len(transcript)})
                # Emit a lightweight heartbeat so users see we're waiting on the model
                self.bus.emit("agent.message", {"role": "info", "content": "Thinking with provider..."})
                # Run provider completion with cooperative cancellation polling
                started = time.time()
                task = asyncio.create_task(self.provider.complete(model or "", transcript))
                reply = None
                while True:
                    if self.cancel_check and self.cancel_check():
                        task.cancel()
                        dur = int((time.time() - started) * 1000)
                        self.bus.emit("provider.end", {"ok": False, "provider": prov_name, "model": model or "", "duration_ms": dur, "cancelled": True})
                        self.bus.emit("agent.message", {"role": "info", "content": "Cancelled while waiting for provider."})
                        self.bus.emit("agent.done", {})
                        return
                    try:
                        reply = await asyncio.wait_for(task, timeout=0.5)
                        break
                    except asyncio.TimeoutError:
                        # keep polling
                        continue
                dur = int((time.time() - started) * 1000)
                self.bus.emit("provider.end", {"ok": True, "provider": prov_name, "model": model or "", "duration_ms": dur, "chars": len(reply or "")})
            except Exception as e:
                emsg = str(e).lower()
                # best-effort provider.end on error
                try:
                    prov_name = getattr(self.provider, "name", "")
                    self.bus.emit("provider.end", {"ok": False, "provider": prov_name, "model": model or "", "error": str(e)})
                except Exception:
                    pass
                if isinstance(e, asyncio.TimeoutError) or "timeout" in emsg:
                    self.bus.emit(
                        "agent.message",
                        {"role": "info", "content": "Provider timed out. Nudging model to act with a JSON 'run' action."},
                    )
                    transcript.append({
                        "role": "user",
                        "content": (
                            "Time is limited. Reply now with exactly one JSON object that proposes a 'run' action "
                            "to gather information (e.g., run tests like 'pytest -q', list files, or grep for failing cases)."
                        ),
                    })
                    continue
                if "no text in response" in emsg or "empty response" in emsg:
                    if no_text_resets < 2:
                        no_text_resets += 1
                        self.bus.emit(
                            "agent.message",
                            {"role": "info", "content": "Provider returned no text; resending initial prompt and task."},
                        )
                        # Reset conversation to initial prompt and add strict JSON reminder
                        transcript = list(original_transcript)
                        transcript.append({
                            "role": "user",
                            "content": (
                                "Respond again with exactly one JSON object only (no extra text, no code fences). "
                                "Use the schema {\"type\":\"run|message|done\",\"cmd?\":string,\"message?\":string,\"thought\":string}."
                            ),
                        })
                        continue
                # Other errors: surface and stop
                self.bus.emit("agent.error", {"error": str(e)})
                break

            # Try to robustly parse a single JSON action from the reply.
            def strip_fences(s: str) -> str:
                s = s.strip()
                if s.startswith("```") and s.endswith("```"):
                    s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
                    s = re.sub(r"\s*```$", "", s)
                return s.strip()

            def parse_objects(s: str):
                objs = []
                dec = json.JSONDecoder()
                i = 0
                n = len(s)
                while i < n:
                    # find next opening brace
                    j = s.find("{", i)
                    if j == -1:
                        break
                    try:
                        obj, end = dec.raw_decode(s, j)
                        objs.append(obj)
                        i = end
                    except Exception:
                        i = j + 1
                return objs

            cleaned = strip_fences(reply)
            objs = parse_objects(cleaned)

            # Detect non-compliant formatting: multiple objects or extra text around JSON
            non_compliant = False
            try:
                dec = json.JSONDecoder()
                s = cleaned.lstrip()
                if s:
                    _, end = dec.raw_decode(s)
                    rest = s[end:].strip()
                    if rest:
                        non_compliant = True
                if len(objs) > 1:
                    non_compliant = True
            except Exception:
                pass
            if not objs:
                # last resort: try to load substring between first { and last }
                try:
                    start = cleaned.index("{")
                    end = cleaned.rindex("}") + 1
                    action = json.loads(cleaned[start:end])
                except Exception:
                    invalid_count += 1
                    self.bus.emit("agent.error", {"error": f"Invalid provider reply (not JSON): {reply[:200]}"})
                    if invalid_count >= 3:
                        break
                    # Ask the model to reformat strictly as JSON
                    correction = (
                        "Your previous reply was not valid JSON. Respond with exactly one JSON object "
                        "matching the required schema with properly escaped quotes and backslashes. "
                        "Do not include any extra text or code fences."
                    )
                    transcript.append({"role": "user", "content": correction})
                    continue
            else:
                # Use the first object as the action; merge 'thought' from the next if present
                action = objs[0]
                if "thought" not in action:
                    for extra in objs[1:]:
                        if isinstance(extra, dict) and extra.get("thought"):
                            action["thought"] = extra.get("thought")
                            break
            invalid_count = 0

            # If the reply contained extra text or multiple objects, request strict JSON-only in next turn
            if non_compliant:
                self.bus.emit("agent.message", {"role": "info", "content": "Model returned extra text; requesting JSON-only format."})
                correction = (
                    "Reply again with exactly one JSON object only (no extra text, no code fences). "
                    "Use the schema {\"type\":\"run|message|done\",\"cmd?\":string,\"message?\":string,\"thought\":string}."
                )
                transcript.append({"role": "user", "content": correction})
                continue

            atype = action.get("type")
            thought = action.get("thought")
            if thought:
                self.bus.emit("agent.message", {"role": "thought", "content": thought})

            if atype == "run":
                cmd = action.get("cmd")
                if not cmd:
                    self.bus.emit("agent.error", {"error": "Missing cmd in run action"})
                    break
                consecutive_message_only = 0
                self.bus.emit("agent.command", {"cmd": cmd})
                # Execute and stream output
                chunks: list[str] = []
                async for stream, text in self.executor.run(cmd, cancel_check=self.cancel_check):
                    if stream == "stdout":
                        self.bus.emit("proc.stdout", {"text": text})
                    else:
                        self.bus.emit("proc.stderr", {"text": text})
                    chunks.append(text)

                full_output = "".join(chunks)
                if self.truncate_limit is not None and self.truncate_limit >= 0:
                    excerpt_text = full_output[-self.truncate_limit:] if self.truncate_limit > 0 else ""
                    transcript.append({"role": "user", "content": f"Command: {cmd}\nOutput (truncated to {self.truncate_limit} chars):\n{excerpt_text}"})
                else:
                    transcript.append({"role": "user", "content": f"Command: {cmd}\nOutput (full):\n{full_output}"})
                continue

            if atype == "message":
                msg = action.get("message") or ""
                self.bus.emit("agent.message", {"role": "assistant", "content": msg})
                transcript.append({"role": "assistant", "content": msg})
                consecutive_message_only += 1
                # If the assistant keeps asking the user, steer it to act.
                steer_patterns = [
                    "can you provide", "please provide", "need the failing", "input for",
                    "share the", "give me", "what is the", "could you",
                ]
                lower = msg.lower()
                if any(p in lower for p in steer_patterns) or consecutive_message_only >= 2:
                    hint = (
                        "No human is available to answer. Do not ask questions. "
                        "Propose a 'run' action now to gather the needed information yourself. "
                        "For example: run tests (e.g., 'pytest -q' or project-specific test commands), "
                        "search the repo (e.g., grep -R -n test .), or inspect files."
                    )
                    self.bus.emit("agent.message", {"role": "info", "content": "Steering model to act without user input."})
                    transcript.append({"role": "user", "content": hint})
                # Prevent infinite loops of messages
                if consecutive_message_only >= 6:
                    self.bus.emit("agent.error", {"error": "Too many assistant messages without actions."})
                    break
                continue

            if atype == "done":
                self.bus.emit("agent.done", {})
                break

            # Unknown action type
            self.bus.emit("agent.error", {"error": f"Unknown action type: {atype}"})
            break
