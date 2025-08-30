import asyncio
import json
import re
import time
import os
from typing import List, Optional, Callable, Dict, Any

from agent_async.core.events import EventBus
from agent_async.exec.local import LocalExecutor
from agent_async.providers.base import Message, Provider


class AgentRunner:
    def __init__(self, event_bus: EventBus, provider: Provider, executor: LocalExecutor, truncate_limit: Optional[int] = None, cancel_check: Optional[Callable[[], bool]] = None):
        self.bus = event_bus
        self.provider = provider
        self.executor = executor
        self.truncate_limit = truncate_limit
        self.cancel_check = cancel_check
        self.is_summarizing = False

    async def run(self, run_id: str, task: str, model: Optional[str]) -> None:
        original_transcript: List[Message] = [
            {"role": "system", "content": self.provider.system_prompt},
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
        invalid_action_count = 0
        consecutive_message_only = 0
        no_text_resets = 0
        # Tunables to avoid giving up too early on format issues
        invalid_limit = int(os.environ.get("AGENT_ASYNC_INVALID_JSON_RETRIES", "3"))
        no_text_limit = int(os.environ.get("AGENT_ASYNC_NO_TEXT_RESETS", "2"))
        think_timeout = int(os.environ.get("AGENT_ASYNC_THINK_TIMEOUT", "600"))
        for step in range(max_steps):
            # Check for cancellation
            if self.cancel_check and self.cancel_check():
                self.bus.emit("agent.message", {"role": "info", "content": "Run cancelled by user."})
                self.bus.emit("agent.done", {})
                break
            try:
                # --- Context Management: Summarization and Truncation ---
                ctx_max = int(os.environ.get("AGENT_ASYNC_CONTEXT_MAX_CHARS", "300000"))
                per_msg_max = int(os.environ.get("AGENT_ASYNC_PER_MESSAGE_MAX_CHARS", "20000"))

                # 1. Summarize if the transcript is too long
                if self._estimate_len(transcript) > ctx_max:
                    await self._summarize_transcript_inplace(transcript, model)

                # 2. Prepare a trimmed view of the transcript for the provider
                trimmed: List[Message] = []
                # Always keep the first system message
                if transcript and transcript[0].get("role") == "system":
                    trimmed.append({"role": "system", "content": transcript[0].get("content", "")})
                    rest = transcript[1:]
                else:
                    rest = transcript[:]

                # Truncate overly long message contents first
                normalized: List[Message] = []
                for m in rest:
                    c = m.get("content", "")
                    if per_msg_max > 0 and len(c) > per_msg_max:
                        c = c[-per_msg_max:]
                    normalized.append({"role": m.get("role", "user"), "content": c})

                # Add from the end until within ctx_max
                acc: List[Message] = []
                for m in reversed(normalized):
                    acc.append(m)
                    candidate = trimmed + list(reversed(acc))
                    if self._estimate_len(candidate) > ctx_max:
                        acc.pop()  # remove last that overflowed
                        break
                send_transcript: List[Message] = trimmed + list(reversed(acc))

                if self._estimate_len(send_transcript) < self._estimate_len(transcript):
                    self.bus.emit("agent.message", {"role": "info", "content": "Context trimmed to fit model limits."})

                # --- Provider Interaction ---
                prov_name = getattr(self.provider, "name", "")
                self.bus.emit("provider.start", {"provider": prov_name, "model": model or "", "messages": len(send_transcript)})
                # Emit a lightweight heartbeat so users see we're waiting on the model
                self.bus.emit("agent.message", {"role": "info", "content": "Thinking with provider..."})
                # Run provider completion with cooperative cancellation polling
                started = time.time()
                task = asyncio.create_task(self.provider.complete(model or "", send_transcript))
                reply = None
                while True:
                    if self.cancel_check and self.cancel_check():
                        task.cancel()
                        dur = int((time.time() - started) * 1000)
                        self.bus.emit("provider.end", {"ok": False, "provider": prov_name, "model": model or "", "duration_ms": dur, "cancelled": True})
                        self.bus.emit("agent.message", {"role": "info", "content": "Cancelled while waiting for provider."})
                        self.bus.emit("agent.done", {})
                        return
                    # Enforce an absolute think timeout
                    if think_timeout and (time.time() - started) >= think_timeout:
                        try:
                            task.cancel()
                        except Exception:
                            pass
                        raise asyncio.TimeoutError(f"provider think timeout after {think_timeout}s")
                    try:
                        # Shield the task so wait_for timeouts do not cancel it
                        reply = await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
                        break
                    except asyncio.TimeoutError:
                        # keep polling
                        continue
                dur = int((time.time() - started) * 1000)
                self.bus.emit("provider.end", {"ok": True, "provider": prov_name, "model": model or "", "duration_ms": dur, "chars": len(reply or "")})
                # Persist raw provider reply to a per-run file and emit a reference event
                try:
                    from pathlib import Path
                    run_dir = Path(self.bus.path).parent
                    out_dir = run_dir / "provider_replies"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    fname = f"step_{step:03}.txt"
                    fpath = out_dir / fname
                    fpath.write_text(reply if isinstance(reply, str) else str(reply))
                    excerpt = (reply or "")[:400] if isinstance(reply, str) else str(reply)[:400]
                    self.bus.emit("provider.reply", {"file": str(fpath.relative_to(run_dir)), "bytes": len((reply or "")), "excerpt": excerpt})
                except Exception:
                    pass
            except asyncio.CancelledError:
                # Gracefully handle cancellation, don't crash the worker thread
                try:
                    prov_name = getattr(self.provider, "name", "")
                except Exception:
                    prov_name = ""
                self.bus.emit("provider.end", {"ok": False, "provider": prov_name, "model": model or "", "cancelled": True})
                self.bus.emit("agent.message", {"role": "info", "content": "Cancelled."})
                self.bus.emit("agent.done", {})
                return
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
                    if no_text_resets < no_text_limit:
                        no_text_resets += 1
                        self.bus.emit(
                            "agent.message",
                            {"role": "info", "content": f"Provider returned no text (attempt {no_text_resets}/{no_text_limit}); nudging model to respond with a JSON 'run' action."},
                        )
                        # Nudge model to respond with a JSON 'run' action
                        transcript.append({
                            "role": "user",
                            "content": (
                                "Your previous response was empty. Please respond with exactly one JSON object. "
                                "For example: {\"type\": \"run\", \"cmd\": \"ls -la\", \"thought\": \"List files\"}"
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
                    # Provide explicit guidance to help the model correct formatting
                    hint = (
                        "Your previous reply was not valid JSON. Reply with exactly one JSON object only (no backticks, no prose). "
                        "Schema: {\"type\":\"run|message|done\",\"cmd?\":string,\"message?\":string,\"thought\":string}. "
                        "The 'cmd' must be a single-line shell command. Do not include raw newlines; use \\n escapes inside the JSON string. "
                        "If you need to write multi-line files, use a single-line printf with \\n (e.g., sh -lc 'printf %s \"line1\\nline2\" > file')."
                    )
                    self.bus.emit("agent.message", {"role": "info", "content": "Requesting JSON-only corrected reply (single-line cmd)."})
                    if invalid_count >= invalid_limit:
                        break
                    # Ask the model to reformat strictly as JSON
                    transcript.append({"role": "user", "content": hint})
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
                    "Use the schema {\"type\":\"run|message|done\",\"cmd?\":string,\"message?\":string,\"thought\":string}. "
                    "The 'cmd' must be a single-line shell command; escape newlines as \\n if needed."
                )
                transcript.append({"role": "user", "content": correction})
                continue

            atype = action.get("type")
            if not isinstance(atype, str):
                atype = None
            thought = action.get("thought")
            if thought:
                self.bus.emit("agent.message", {"role": "thought", "content": thought})

            # Handle missing/unknown type by inference or by requesting correction
            if atype not in ("run", "message", "done"):
                # Log raw reply excerpt for debugging
                try:
                    raw_excerpt = cleaned[:500]
                    self.bus.emit("agent.message", {"role": "info", "content": f"Provider reply missing/unknown type; inferring action. Excerpt: {raw_excerpt}"})
                except Exception:
                    pass
                # Infer a reasonable default
                if isinstance(action.get("cmd"), str) and action.get("cmd").strip():
                    atype = "run"
                    self.bus.emit("agent.message", {"role": "info", "content": "Inferred action type 'run' from 'cmd' field."})
                elif isinstance(action.get("message"), str) and action.get("message").strip():
                    atype = "message"
                    self.bus.emit("agent.message", {"role": "info", "content": "Inferred action type 'message' from 'message' field."})
                else:
                    invalid_action_count += 1
                    self.bus.emit("agent.error", {"error": f"Unknown action type: {action.get('type')}"})
                    if invalid_action_count >= 3:
                        break
                    # Ask the model to resend with a proper 'type'
                    transcript.append({
                        "role": "user",
                        "content": (
                            "Your reply lacked a valid 'type'. Reply again with exactly one JSON object only, "
                            "using the schema {\"type\":\"run|message|done\",\"cmd?\":string,\"message?\":string,\"thought\":string}."
                        ),
                    })
                    continue

            if atype == "run":
                cmd = action.get("cmd")
                if not cmd:
                    self.bus.emit("agent.error", {"error": "Missing cmd in run action"})
                    break
                # Guard against multi-line commands by auto-correcting to a single line,
                # but allow intentional multi-line scripts (e.g., heredoc, sh -c '...').
                if isinstance(cmd, str) and ("\n" in cmd or "\r" in cmd):
                    # Simple heuristic: if it looks like a script for an interpreter, don't join with &&.
                    # This is not perfect but covers many common cases.
                    first_line = cmd.strip().splitlines()[0]
                    is_script = first_line.startswith(("sh -c", "bash -c", "python -c", "python3 -c")) or "<<" in first_line
                    if not is_script:
                        self.bus.emit("agent.message", {"role": "info", "content": "Run cmd contained raw newlines; auto-correcting to a single-line command."})
                        lines = [line.strip() for line in cmd.splitlines() if line.strip()]
                        cmd = " && ".join(lines)
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
                # If a message is provided alongside done, show it
                msg = action.get("message")
                if isinstance(msg, str) and msg.strip():
                    self.bus.emit("agent.message", {"role": "assistant", "content": msg})
                    transcript.append({"role": "assistant", "content": msg})
                self.bus.emit("agent.done", {})
                break

            # Unknown action type (fallback)
            self.bus.emit("agent.error", {"error": f"Unknown action type: {atype}"})
            invalid_action_count += 1
            if invalid_action_count >= 3:
                break
            transcript.append({
                "role": "user",
                "content": (
                    "Reply again with one JSON object only using {\"type\":\"run|message|done\",\"cmd?\":string,\"message?\":string,\"thought\":string}."
                ),
            })
            continue

    def _estimate_len(self, msgs: List[Message]) -> int:
        total = 0
        for m in msgs:
            total += len(m.get("role", "")) + len(m.get("content", "")) + 8
        return total

    async def _summarize_transcript_inplace(self, transcript: List[Message], model: Optional[str]):
        if self.is_summarizing:
            return  # Avoid recursive summarization
        self.is_summarizing = True
        
        try:
            self.bus.emit("agent.message", {"role": "info", "content": "Context is full, attempting to summarize..."})

            # 1. Identify what to summarize. Keep system prompt, initial task, and last N messages.
            if len(transcript) < 10:  # Don't summarize if transcript is short
                self.bus.emit("agent.message", {"role": "info", "content": "Transcript too short to summarize, will use truncation instead."})
                return

            # Find boundaries for summarization
            # Keep system prompt + initial task
            keep_at_start = 2
            # Keep last 4 messages (2 pairs of user/assistant or user/tool_output)
            keep_at_end = 4
            
            if len(transcript) <= keep_at_start + keep_at_end:
                self.bus.emit("agent.message", {"role": "info", "content": "Not enough messages to summarize, will use truncation."})
                return

            start_messages = transcript[:keep_at_start]
            end_messages = transcript[-keep_at_end:]
            messages_to_summarize = transcript[keep_at_start:-keep_at_end]

            if not messages_to_summarize:
                return  # Nothing to summarize

            # 2. Create summarization request
            # Flatten the messages to summarize into a single string for the prompt
            condensed_log = "\n".join([f"<{m['role']}>\n{m['content']}\n</{m['role']}>" for m in messages_to_summarize])

            summary_request_transcript: List[Message] = [
                {"role": "system", "content": (
                    "You are an expert summarizer. Your task is to create a concise summary of an ongoing AI agent's work log. "
                    "The summary should preserve key information: what has been tried, what were the outcomes (successes and failures), "
                    "what files were changed, and what the agent was trying to do last. "
                    "This summary will replace the original log to save space, so it must be accurate and informative for the agent to continue its task."
                )},
                {"role": "user", "content": f"Please summarize this conversation log:\n\n{condensed_log}"}
            ]

            # 3. Call provider to get summary
            self.bus.emit("provider.start", {"provider": "summary", "model": model or ""})
            summary_text = await self.provider.complete(model or "", summary_request_transcript)
            self.bus.emit("provider.end", {"ok": True, "provider": "summary", "model": model or ""})

            if not summary_text or not summary_text.strip():
                self.bus.emit("agent.message", {"role": "info", "content": "Summarization failed: provider returned empty text."})
                return

            self.bus.emit("agent.message", {"role": "info", "content": f"Summary created:\n---\n{summary_text}\n---"})

            # 4. Reconstruct the transcript
            summary_message = {
                "role": "user", 
                "content": f"The following is a summary of the work done so far to catch you up:\n{summary_text}"
            }
            
            new_transcript = start_messages + [summary_message] + end_messages
            
            # Replace original transcript
            transcript.clear()
            transcript.extend(new_transcript)

        except Exception as e:
            self.bus.emit("agent.error", {"error": f"Failed to summarize transcript: {e}"})
        finally:
            self.is_summarizing = False
