"""orchestrator/autonomous — an autonomous coding agent loop, entirely over host.contract.call.

It binds no other brick directly. It asks the kernel for ``inference``, ``context``, ``tools``,
``sandbox``, ``memory`` and ``conversation`` by contract name; whichever bricks the stack binds
run. ``inference`` is required; everything else degrades to a skipped capability when unbound.

The loop, per turn: inspect → act with tools → observe → recover → verify. What makes it
*autonomous* rather than a single generate call:

* **Objective verification.** The goal is split into objectives. An objective is not closed
  because the model says so — the loop decides what evidence closes it (a command that exits 0, a
  file that now exists, a diff on disk) and checks it before moving on.
* **Git awareness.** Working-tree state is snapshotted before the run and after every objective;
  the summary reports the files that actually changed, not an assertion of success.
* **Failure recovery.** A failed command or tool result is fed back with its real output and the
  model is asked to change approach. Consecutive failures are capped; at the cap the loop stops
  and escalates to the caller instead of looping forever.
* **Context management.** The working transcript is compacted automatically (summarise the
  middle, keep the head and tail) once it grows past a threshold.

Integration with what already shipped:

* ``session_id`` comes from the binding **config**, never the wire — the CLI injects it so the id
  on screen, the conversation-history key and the ``--resume`` key are one id.
* ``compact`` is implemented non-destructively: append a summary turn tagged
  ``metadata.kind = "compaction"``; later ``run`` calls load only that summary and what follows.
* Token ``usage`` is emitted only when the provider reports it.
* Every long operation is a plain ``await`` on ``host.contract.call``; ``$/cancel`` cascades
  through the kernel with no second mechanism here.
"""

from __future__ import annotations

import re

from veridian.sdk import Brick, BrickError, rpc, run

_CONTRACT_NOT_BOUND = -32002
_PERMISSION_DENIED = -32001

_MAX_ITERATIONS = 20
_MAX_CONSECUTIVE_FAILURES = 3
_COMPACT_OVER_CHARS = 48_000  # ~12k tokens of working transcript
_TOOL_OUTPUT_CAP = 8_000

_SPLIT = re.compile(r"\s+(?:and then|then|and)\s+|[\n;]+|(?<=\S)\.\s+(?=[A-Z])", re.I)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
_VERIFY_BY_COMMAND = re.compile(
    r"\b(test|tests|pytest|unittest|run |runs |execute|build|builds|compile|compiles|lint|"
    r"typecheck|npm test|cargo|make )\b",
    re.I,
)
_VERIFY_BY_FILE = re.compile(
    r"\b(create|creates|add |adds |write |writes |implement|implements|new file|edit |edits |"
    r"rename|delete|remove )\b",
    re.I,
)

_SYSTEM = """You are an autonomous coding agent working in a real repository.

Operating rules:
- Work through the objectives below in order. Use tools to inspect before you change anything.
- After each change, verify it: run the relevant command or re-read the file. Do not claim
  something works without evidence.
- When a command fails, read its actual output, form a hypothesis, and try a different approach.
- When the current objective is genuinely done and verified, reply with a short status and NO
  tool calls. The loop will check your evidence and either advance you or hand back what is still
  missing.
- When every objective is complete, reply with a concise final summary and no tool calls.
"""


def _text_of(block: dict) -> str:
    """The text of a content block. A ``text`` key that is missing *or present and null* both
    mean "no text" — a small local model (e.g. a reasoning model that spent its whole token
    budget thinking) sends ``"text": null``, and ``block.get("text", "")`` returns that ``None``
    rather than the default. Left as-is it is concatenated with a string downstream and the run
    dies with a ``TypeError``."""
    text = block.get("text")
    return text if isinstance(text, str) else ""


def _normalise_message(message: dict) -> dict:
    """Coerce a provider- or store-supplied message into the shape the loop assumes: ``content``
    is either a string or a list of well-formed blocks, and every text block's ``text`` is a
    real string. Done once here, at the boundary where an inference result is parsed, rather
    than defended against at each of the many use sites downstream."""
    content = message.get("content")
    if content is None:
        message["content"] = []
    elif isinstance(content, list):
        clean: list[dict] = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and not isinstance(b.get("text"), str):
                b = {**b, "text": ""}
            clean.append(b)
        message["content"] = clean
    return message


def _turn_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(
            _text_of(b) for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return "" if content is None else str(content)


def _after_last_compaction(entries: list[dict]) -> list[dict]:
    last = -1
    for i, e in enumerate(entries):
        if (e.get("metadata") or {}).get("kind") == "compaction":
            last = i
    return entries[last:] if last >= 0 else entries


def _decompose(goal: str) -> list[str]:
    parts = [
        _NUMBERED.sub("", p).strip(" .\t")
        for p in _SPLIT.split(goal)
        if p and p.strip(" .\t")
    ]
    parts = [p for p in parts if len(p) > 2]
    return parts or [goal.strip()]


def _blocks(message: dict) -> list[dict]:
    content = message.get("content")
    if isinstance(content, list):
        return [b for b in content if isinstance(b, dict)]
    if content is None:
        return []
    return [{"type": "text", "text": str(content)}]


_CHATTY = re.compile(
    r"^\s*(?:hi|hey+|hello|yo|sup|howdy|thanks|thank you|thx|ty|cheers|bye|goodbye|see you|"
    r"good (?:morning|afternoon|evening|night)|how are you|how'?s it going|how are things|"
    r"who are you|what are you|what can you do|nice to meet you|ok|okay|cool|great|awesome|"
    r"nvm|never ?mind)\b[\s!.?…]*$",
    re.I,
)


def _is_conversational(goal: str) -> bool:
    """A greeting or pleasantry, not a task. Such input should get a plain reply — not be
    decomposed into an objective and run through the verify/evidence loop."""
    return bool(_CHATTY.match(goal.strip()))


class OrchestratorAutonomous(Brick):
    name = "orchestrator/autonomous"
    version = "0.1.0"
    implements = {"orchestrator": ["run", "compact"]}

    # -- host helpers -------------------------------------------------------

    async def _try(self, contract: str, method: str, params: dict):
        """host.contract.call, returning None only when the contract is not bound."""
        try:
            return await self.host.contract_call(contract, method, params)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) == _CONTRACT_NOT_BOUND:
                return None
            raise

    async def _opt(self, contract: str, method: str, params: dict):
        """Like ``_try`` but also a no-op when the capability was never granted."""
        try:
            return await self.host.contract_call(contract, method, params)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) in (_CONTRACT_NOT_BOUND, _PERMISSION_DENIED):
                return None
            raise

    # -- git snapshots (through the tools contract) ---------------------

    async def _git_files(self) -> set[str] | None:
        res = await self._opt("tools", "invoke", {"name": "git_status", "input": {}})
        if not res or res.get("is_error"):
            return None
        files: set[str] = set()
        for line in res.get("output", "").splitlines():
            line = line.rstrip()
            if not line or line.startswith("##"):
                continue
            files.add(line[3:].strip() if len(line) > 3 else line.strip())
        return files

    async def _git_diffstat(self) -> str:
        res = await self._opt("tools", "invoke", {"name": "git_diff", "input": {}})
        if not res or res.get("is_error"):
            return ""
        return res.get("output", "")[:2000]

    # -- context compaction of the live transcript --------------------

    async def _maybe_compact_transcript(self, transcript: list[dict], emit) -> None:
        size = sum(len(_turn_text(m)) for m in transcript)
        if size < _COMPACT_OVER_CHARS or len(transcript) <= 8:
            return
        head, middle, tail = transcript[:2], transcript[2:-4], transcript[-4:]
        convo = "\n\n".join(f"{m.get('role', '?')}: {_turn_text(m)}" for m in middle)
        try:
            gen = await self.host.contract_call(
                "inference",
                "generate",
                {
                    "messages": [
                        {"role": "system", "content": "Compress this agent transcript. Keep "
                         "decisions, file paths, commands run and their outcomes, and open "
                         "threads. No preamble."},
                        {"role": "user", "content": convo},
                    ],
                    "max_tokens": 512,
                },
            )
        except Exception:  # noqa: BLE001 - compaction is best-effort
            return
        summary = "".join(
            _text_of(b) for b in _blocks(gen["message"]) if b.get("type") == "text"
        ).strip()
        if not summary:
            return
        transcript[:] = [
            *head,
            {"role": "assistant", "content": f"[COMPACTED WORKING CONTEXT]\n{summary}"},
            *tail,
        ]
        await emit("log", message=f"compacted working context ({size} chars -> tail + summary)")

    # -- verification ----------------------------------------------------

    @staticmethod
    def _successful_command_ran(transcript: list[dict]) -> tuple[bool, str]:
        """Scan the transcript for run_command tool results: whether one exited 0, and the tail of
        the most recent failing one."""
        ok = False
        last_fail = ""
        for m in transcript:
            if m.get("role") != "tool":
                continue
            for b in _blocks(m):
                if b.get("type") != "tool_result":
                    continue
                content = b.get("content", "")
                if "$ " not in content and "exit=" not in content:
                    continue
                if b.get("is_error"):
                    last_fail = content[-600:]
                else:
                    ok = True
        return ok, last_fail

    async def _verify(self, objective: str, git_before: set[str] | None,
                      transcript: list[dict]) -> tuple[bool, str]:
        if _VERIFY_BY_COMMAND.search(objective):
            ran_ok, last_fail = self._successful_command_ran(transcript)
            if ran_ok:
                return True, "a command run for this objective exited 0"
            if last_fail:
                return False, f"the verifying command still fails:\n{last_fail}"
            return False, "this objective needs a command to prove it works; run it via run_command"

        if _VERIFY_BY_FILE.search(objective):
            now = await self._git_files()
            if now is None:
                # no git available — fall back to: did an edit/write tool succeed?
                for m in transcript:
                    if m.get("role") != "tool":
                        continue
                    for b in _blocks(m):
                        if b.get("type") == "tool_result" and not b.get("is_error") and (
                            "wrote " in b.get("content", "") or "edited " in b.get("content", "")
                        ):
                            return True, "a write/edit tool call succeeded"
                return False, "no successful write_file / edit_file call is visible for this objective"
            changed = now - (git_before or set())
            if changed or (now and git_before is None):
                return True, f"working tree changed: {', '.join(sorted(changed)) or 'yes'}"
            return False, "no change to the working tree — nothing was written to disk"

        return True, "no automatic evidence rule for this objective; accepted on the model's report"

    # -- the run loop --------------------------------------------------

    @rpc("orchestrator.run", streaming=True)
    async def run_(self, params, ctx):
        goal = params["goal"]
        max_iter = int((params.get("limits") or {}).get("max_iterations", _MAX_ITERATIONS))
        max_failures = int(self.config.get("max_recovery", _MAX_CONSECUTIVE_FAILURES))
        # session id is a deployment concern injected into binding config by the CLI — never the wire
        session_id = str(self.config.get("session_id") or "default")

        async def emit(event: str, **data):
            await ctx.emit_delta({"event": event, "data": data})

        await emit("log", message=f"goal: {goal}")

        # 1. context (repo map + AGENT.md guidelines + top chunks) --------------
        context_text = ""
        retrieved = await self._try("context", "retrieve", {"query": goal, "k": 6})
        if retrieved and retrieved.get("chunks"):
            context_text = "\n\n".join(
                f"# {c['path']}\n{c['text']}" for c in retrieved["chunks"]
            )
            await emit("log", message=f"retrieved {len(retrieved['chunks'])} context chunk(s)")

        # optional model context window, for annotating usage deltas
        context_window = None
        models = await self._opt("inference", "models", {})
        for m in (models or {}).get("models", []):
            if m.get("context_window"):
                context_window = m["context_window"]
                break

        # 2. tools ------------------------------------------------------------
        tool_listing = await self._try("tools", "list", {})
        tool_schemas = list(tool_listing["tools"]) if tool_listing else []
        has_run_command = any(t["name"] == "run_command" for t in tool_schemas)
        sandbox_ok = await self._opt("sandbox", "reset", {}) is not None
        if not has_run_command and sandbox_ok:
            tool_schemas.append(
                {
                    "name": "run_command",
                    "description": "Run a shell command in the workspace via the sandbox. Returns "
                                   "exit code, stdout and stderr.",
                    "input_schema": {
                        "type": "object",
                        "required": ["command"],
                        "properties": {
                            "command": {"type": "string"},
                            "timeout_ms": {"type": "integer"},
                        },
                    },
                }
            )
            has_run_command = True

        # 3. objectives + git baseline -------------------------------------
        # A greeting or pleasantry is not a task: answer it in one turn, with no objective, no
        # verify/evidence loop, and no git snapshot. Everything else is decomposed and run.
        conversational = _is_conversational(goal)
        objectives = [] if conversational else _decompose(goal)
        git_before = None if conversational else await self._git_files()
        if conversational:
            await emit("step", kind="chat")
        else:
            await emit("step", kind="plan", steps=objectives)

        if conversational:
            system_text = (
                "You are a helpful assistant embedded in a coding agent. The user has said "
                "something conversational rather than asking for work. Reply briefly and directly, "
                "with no tool calls."
            )
        else:
            system_text = (
                _SYSTEM
                + "\nObjectives:\n"
                + "\n".join(f"{i + 1}. {o}" for i, o in enumerate(objectives))
            )
        if context_text:
            system_text += f"\n\nRepository context:\n{context_text}\n"
        transcript: list[dict] = [{"role": "system", "content": system_text}]

        history = await self._opt(
            "conversation", "load", {"session_id": session_id, "limit": 40}
        )
        if history and history.get("messages"):
            entries = _after_last_compaction(history["messages"])
            transcript.extend(e["message"] for e in entries)
            note = f"loaded {len(entries)} prior turn(s) from session {session_id!r}"
            if len(entries) < len(history["messages"]):
                note += " (post-compaction)"
            await emit("log", message=note)

        transcript.append({"role": "user", "content": goal})
        await self._opt(
            "conversation", "append",
            {"session_id": session_id, "message": {"role": "user", "content": goal}},
        )

        iterations = 0
        obj_idx = 0
        consecutive_failures = 0
        status = "failed"
        summary = ""
        in_tot = out_tot = 0
        saw_usage = False

        while iterations < max_iter:
            iterations += 1
            await self._maybe_compact_transcript(transcript, emit)

            gen_params: dict = {"messages": transcript, "max_tokens": 2048}
            if tool_schemas:
                gen_params["tools"] = tool_schemas
            result = await self.host.contract_call("inference", "generate", gen_params)
            message = _normalise_message(result["message"])
            transcript.append(message)

            usage = result.get("usage")
            if usage:
                saw_usage = True
                turn_in = int(usage.get("input_tokens", 0) or 0)
                in_tot += turn_in
                out_tot += int(usage.get("output_tokens", 0) or 0)
                data = {"input_tokens": in_tot, "output_tokens": out_tot, "context_tokens": turn_in}
                if context_window:
                    data["context_window"] = context_window
                await emit("usage", **data)

            blocks = _blocks(message)
            text = "".join(_text_of(b) for b in blocks if b.get("type") == "text")
            tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
            if text:
                await emit("message", text=text)

            # Some small models emit an empty completion. Left as-is that turn becomes an assistant
            # message with null content, which the next generate call rejects. Normalise it and
            # treat it as a non-advancing turn.
            if not text.strip() and not tool_uses:
                message["content"] = [{"type": "text", "text": "(the model produced no output)"}]
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    where = (
                        f"objective {obj_idx + 1} ({objectives[obj_idx]!r})"
                        if obj_idx < len(objectives)
                        else "this request"
                    )
                    summary = (
                        f"The model returned no output {consecutive_failures} turns in a row on "
                        f"{where}. Human input needed."
                    )
                    await emit("message", text=summary)
                    status = "failed"
                    break
                transcript.append(
                    {"role": "user", "content": "You returned nothing. State your next concrete "
                     "step and take it with a tool call, or give the final summary if every "
                     "objective is done and verified."}
                )
                continue

            # -- act ------------------------------------------------------
            if tool_uses:
                tool_results = []
                any_error = False
                for tu in tool_uses:
                    await emit("tool", name=tu["name"], input=tu["input"])
                    obs, is_err = await self._dispatch_tool(tu, has_run_command)
                    any_error = any_error or is_err
                    await emit("tool", name=tu["name"], output=obs[:2000], is_error=is_err)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": obs[:_TOOL_OUTPUT_CAP],
                            "is_error": is_err,
                        }
                    )
                transcript.append({"role": "tool", "content": tool_results})

                if any_error:
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        where = (
                            f"objective {obj_idx + 1} ({objectives[obj_idx]!r})"
                            if obj_idx < len(objectives)
                            else "this request"
                        )
                        summary = (
                            f"Stopped after {consecutive_failures} consecutive failing steps on "
                            f"{where}. Last tool output is in the transcript; human input needed."
                        )
                        await emit("message", text=summary)
                        status = "failed"
                        break
                    transcript.append(
                        {
                            "role": "user",
                            "content": "That step failed. Read the output above, state briefly "
                            "what went wrong, and try a different approach.",
                        }
                    )
                else:
                    consecutive_failures = 0
                continue

            # -- no tool calls -----------------------------------------------
            if conversational:
                # A plain reply to a plain message: done, nothing to verify.
                consecutive_failures = 0
                status = "completed"
                summary = text or "(no reply)"
                break

            # -- verify the current objective -------------------------------
            ok, evidence = await self._verify(objectives[obj_idx], git_before, transcript)
            await emit("step", kind="verify", objective=objectives[obj_idx], ok=ok, evidence=evidence)

            if not ok:
                consecutive_failures += 1
                if consecutive_failures >= max_failures:
                    summary = (
                        f"Could not verify objective {obj_idx + 1} ({objectives[obj_idx]!r}) after "
                        f"{consecutive_failures} attempts: {evidence}. Human input needed."
                    )
                    await emit("message", text=summary)
                    status = "failed"
                    break
                transcript.append(
                    {"role": "user", "content": f"Not verified yet: {evidence} "
                     f"Address that, then continue."}
                )
                continue

            consecutive_failures = 0
            obj_idx += 1
            git_before = await self._git_files()
            if obj_idx >= len(objectives):
                status = "completed"
                summary = text or "all objectives verified"
                break
            transcript.append(
                {"role": "user", "content": f"Objective {obj_idx} verified ({evidence}). "
                 f"Next objective: {objectives[obj_idx]}"}
            )
        else:
            status = "max_iterations"

        # -- close out: real git evidence + persistence -----------------
        if not summary:
            summary = f"stopped after {iterations} iteration(s) with status {status}"
        parts = [summary]
        if not conversational:
            changed_after = await self._git_files()
            diffstat = await self._git_diffstat()
            if changed_after:
                parts.append("changed files: " + ", ".join(sorted(changed_after)))
            if diffstat:
                parts.append("diff:\n" + diffstat)
        full_summary = "\n\n".join(parts)

        # A chat turn is not a run worth recording in long-term memory; it still goes to the
        # verbatim conversation log below.
        if not conversational:
            await self._try(
                "memory",
                "write",
                {
                    "content": f"goal: {goal}\nstatus: {status}\niterations: {iterations}\n{full_summary}",
                    "tags": ["orchestrator-run", status],
                },
            )
        await self._opt(
            "conversation",
            "append",
            {"session_id": session_id, "message": {"role": "assistant", "content": full_summary}},
        )

        out: dict = {"status": status, "iterations": iterations, "summary": full_summary}
        if saw_usage:
            out["usage"] = {"input_tokens": in_tot, "output_tokens": out_tot}
        return out

    async def _dispatch_tool(self, tu: dict, has_run_command: bool) -> tuple[str, bool]:
        name, inp = tu["name"], tu.get("input", {})
        if name == "run_command" and not has_run_command:
            return "run_command is not available in this session", True
        # run_command may be served by the tools brick itself, or (fallback) by us via sandbox
        if name == "run_command":
            served = await self._try("tools", "invoke", {"name": name, "input": inp})
            if served is not None:
                return served.get("output", ""), bool(served.get("is_error"))
            ex = await self._try(
                "sandbox",
                "exec",
                {"command": inp["command"], "timeout_ms": int(inp.get("timeout_ms", 30000))},
            )
            if ex is None:
                return "no sandbox bound; cannot run commands", True
            return (
                f"exit={ex['exit_code']}\n{ex['stdout']}\n{ex['stderr']}",
                ex["exit_code"] != 0,
            )
        inv = await self._try("tools", "invoke", {"name": name, "input": inp})
        if inv is None:
            return f"no tools brick bound; cannot run {name!r}", True
        return inv.get("output", ""), bool(inv.get("is_error"))

    # -- compact (live /compact command) ------------------------------

    @rpc("orchestrator.compact")
    async def compact(self, params, ctx):
        """Compress the working context this orchestrator holds for a session, non-destructively:
        summarise the turns since the last compaction and append the summary as one turn tagged
        ``metadata.kind = "compaction"``. Later ``run`` calls load only that summary and what
        follows it. The verbatim turns stay in the log on disk."""
        session_id = str(
            params.get("session_id") or self.config.get("session_id") or "default"
        )

        history = await self._opt("conversation", "load", {"session_id": session_id})
        if not history or not history.get("messages"):
            return {"status": "noop", "turns_before": 0, "turns_after": 0}

        live = _after_last_compaction(history["messages"])
        if live and (live[0].get("metadata") or {}).get("kind") == "compaction":
            body = live[1:]
        else:
            body = live
        if len(body) <= 2:
            return {"status": "noop", "turns_before": len(body), "turns_after": len(body)}

        convo = "\n\n".join(
            f"{e['message'].get('role', '?')}: {_turn_text(e['message'])}" for e in body
        )
        gen = await self.host.contract_call(
            "inference",
            "generate",
            {
                "messages": [
                    {
                        "role": "system",
                        "content": "You compress conversations. Reply with a compact summary that "
                        "keeps decisions, file paths, commands run, and open threads so a later "
                        "session can continue. No preamble.",
                    },
                    {"role": "user", "content": convo},
                ],
                "max_tokens": 512,
            },
        )
        summary = "".join(
            _text_of(b) for b in _blocks(gen["message"]) if b.get("type") == "text"
        ).strip() or "(summary unavailable)"

        await self._opt(
            "conversation",
            "append",
            {
                "session_id": session_id,
                "message": {"role": "assistant", "content": summary},
                "metadata": {"kind": "compaction"},
            },
        )
        return {
            "status": "compacted",
            "summary": summary,
            "turns_before": len(body),
            "turns_after": 1,
        }


if __name__ == "__main__":
    run(OrchestratorAutonomous())
