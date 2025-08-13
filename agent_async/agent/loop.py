import asyncio
import json
import re
from typing import List, Optional

from agent_async.core.events import EventBus
from agent_async.exec.local import LocalExecutor
from agent_async.providers.base import Message, Provider
from .prompt import SYSTEM_PROMPT


class AgentRunner:
    def __init__(self, event_bus: EventBus, provider: Provider, executor: LocalExecutor, truncate_limit: Optional[int] = None):
        self.bus = event_bus
        self.provider = provider
        self.executor = executor
        self.truncate_limit = truncate_limit

    async def run(self, run_id: str, task: str, model: Optional[str]) -> None:
        original_transcript: List[Message] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Task: {task}"},
        ]
        transcript: List[Message] = list(original_transcript)

        max_steps = 50
        invalid_count = 0
        no_text_resets = 0
        for step in range(max_steps):
            try:
                reply = await self.provider.complete(model or "", transcript)
            except Exception as e:
                emsg = str(e).lower()
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
                self.bus.emit("agent.command", {"cmd": cmd})
                # Execute and stream output
                chunks: list[str] = []
                async for stream, text in self.executor.run(cmd):
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
                # Ask provider for next step using the message as context
                continue

            if atype == "done":
                self.bus.emit("agent.done", {})
                break

            # Unknown action type
            self.bus.emit("agent.error", {"error": f"Unknown action type: {atype}"})
            break
