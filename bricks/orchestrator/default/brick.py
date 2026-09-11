"""orchestrator/default — the agent loop, entirely over host.contract.call.

It holds no reference to any other brick. It asks the kernel for ``context``, ``planner``,
``inference``, ``tools``, and ``memory`` by contract name; whichever bricks the stack binds are
what run. Because the loop itself is a brick, replacing this brick replaces the agent
architecture — that is the whole point of Veridian.

``inference`` is required. ``context``, ``planner``, ``tools``, and ``memory`` are optional: if a
contract is not bound the loop skips that capability instead of failing.
"""

from __future__ import annotations

from veridian.sdk import Brick, BrickError, rpc, run

_CONTRACT_NOT_BOUND = -32002
_PERMISSION_DENIED = -32001

_MAX_ITERATIONS = 12


def _turn_text(message: dict) -> str:
    """The plain text of a stored turn, blocks flattened."""
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def _after_last_compaction(entries: list[dict]) -> list[dict]:
    """The tail of ``entries`` from the most recent ``metadata.kind == "compaction"`` marker
    onward (marker included, since its summary *is* the compressed context). The whole list when
    there is no marker."""
    last = -1
    for i, e in enumerate(entries):
        if (e.get("metadata") or {}).get("kind") == "compaction":
            last = i
    return entries[last:] if last >= 0 else entries


class OrchestratorDefault(Brick):
    name = "orchestrator/default"
    version = "0.1.0"
    implements = {"orchestrator": ["run", "compact"]}

    async def _try(self, contract: str, method: str, params: dict):
        """host.contract.call, returning None if the contract simply isn't bound."""
        try:
            return await self.host.contract_call(contract, method, params)
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "code", None)
            if code == _CONTRACT_NOT_BOUND:
                return None
            raise

    async def _opt(self, contract: str, method: str, params: dict):
        """Like ``_try`` but also a no-op when the capability was never granted. For genuinely
        optional side-channels (``conversation`` history) that a stack may simply omit."""
        try:
            return await self.host.contract_call(contract, method, params)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) in (_CONTRACT_NOT_BOUND, _PERMISSION_DENIED):
                return None
            raise

    @rpc("orchestrator.run", streaming=True)
    async def run_(self, params, ctx):
        goal = params["goal"]
        max_iter = int((params.get("limits") or {}).get("max_iterations", _MAX_ITERATIONS))
        # Optional local chat history. A stack that binds `conversation` gets cross-run continuity;
        # one that doesn't behaves exactly as before. The session id is a deployment concern in
        # Version 0, so it comes from brick config, not the wire.
        session_id = str(self.config.get("session_id") or "default")

        async def emit(event: str, **data):
            await ctx.emit_delta({"event": event, "data": data})

        await emit("log", message=f"goal: {goal}")

        # 1. context ------------------------------------------------------------------
        context_text = ""
        retrieved = await self._try("context", "retrieve", {"query": goal, "k": 6})
        if retrieved and retrieved.get("chunks"):
            context_text = "\n\n".join(
                f"# {c['path']}\n{c['text']}" for c in retrieved["chunks"][:6]
            )
            await emit("log", message=f"retrieved {len(retrieved['chunks'])} context chunks")

        # 2. plan -------------------------------------------------------------------
        plan = await self._try("planner", "plan", {"goal": goal})
        plan_id = plan["plan_id"] if plan else None
        if plan:
            await emit("step", kind="plan", steps=[s["description"] for s in plan["steps"]])

        # context window for the bound model, if the inference brick reports its catalogue. Used
        # only to annotate the usage deltas; absent is fine.
        context_window = None
        models = await self._opt("inference", "models", {})
        for m in (models or {}).get("models", []):
            if m.get("context_window"):
                context_window = m["context_window"]
                break

        # 3. tool catalogue -------------------------------------------------------------
        tool_listing = await self._try("tools", "list", {})
        tool_schemas = list(tool_listing["tools"]) if tool_listing else []
        # If a sandbox is bound *and reachable*, offer a run_command tool backed by sandbox.exec.
        # ``_opt`` (not ``_try``) so a session that withholds ``contract:sandbox`` — e.g. plan
        # mode — degrades to no command execution instead of failing the run.
        sandbox_probe = await self._opt("sandbox", "reset", {})
        self._has_sandbox = sandbox_probe is not None
        if self._has_sandbox:
            tool_schemas.append(
                {
                    "name": "run_command",
                    "description": "Run a shell command in the workspace. Returns exit code and output.",
                    "input_schema": {
                        "type": "object",
                        "required": ["command"],
                        "properties": {"command": {"type": "string"}, "timeout_ms": {"type": "integer"}},
                    },
                }
            )

        # 4. the loop -------------------------------------------------------------------
        transcript: list[dict] = [
            {
                "role": "system",
                "content": (
                    "You are a coding agent. Work towards the goal using the provided tools. "
                    "When the goal is met, reply with a short summary and no tool calls.\n\n"
                    + (f"Relevant context:\n{context_text}\n" if context_text else "")
                ),
            }
        ]

        # prior turns from local chat history, if a `conversation` brick is bound. When the session
        # has been /compact-ed, load only the compaction summary and everything after it.
        history = await self._opt("conversation", "load", {"session_id": session_id, "limit": 40})
        if history and history.get("messages"):
            entries = _after_last_compaction(history["messages"])
            transcript.extend(e["message"] for e in entries)
            note = f"loaded {len(entries)} prior turn(s) from session {session_id!r}"
            if len(entries) < len(history["messages"]):
                note += " (post-compaction)"
            await emit("log", message=note)

        transcript.append({"role": "user", "content": goal})
        # persist the user turn now, so it survives even a run that fails at inference
        await self._opt(
            "conversation", "append", {"session_id": session_id, "message": {"role": "user", "content": goal}}
        )

        iterations = 0
        status = "failed"
        summary = ""
        in_tot = 0
        out_tot = 0
        saw_usage = False

        while iterations < max_iter:
            iterations += 1

            if plan_id:
                nxt = await self._try("planner", "next", {"plan_id": plan_id, "observations": []})
                step = nxt["step"] if nxt else None
                if step is None:
                    status = "completed"
                    break
                await emit("step", kind="active", id=step["id"], description=step["description"])
                transcript.append({"role": "user", "content": f"Current step: {step['description']}"})

            gen_params: dict = {"messages": transcript, "max_tokens": 1024}
            if tool_schemas:
                gen_params["tools"] = tool_schemas
            result = await self.host.contract_call("inference", "generate", gen_params)
            message = result["message"]
            transcript.append(message)

            # token usage — only if the provider reported it. A provider that reports nothing
            # emits no usage delta at all, so the UI shows the field as absent, not zero.
            usage = result.get("usage")
            if usage:
                saw_usage = True
                turn_in = int(usage.get("input_tokens", 0) or 0)
                in_tot += turn_in
                out_tot += int(usage.get("output_tokens", 0) or 0)
                data = {
                    "input_tokens": in_tot,
                    "output_tokens": out_tot,
                    # context fill = tokens the last request actually carried
                    "context_tokens": turn_in,
                }
                if context_window:
                    data["context_window"] = context_window
                await emit("usage", **data)

            blocks = message["content"] if isinstance(message["content"], list) else [
                {"type": "text", "text": message["content"]}
            ]
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
            if text:
                await emit("message", text=text)

            if not tool_uses:
                summary = text or "done"
                status = "completed" if not plan_id else status
                if not plan_id:
                    break
                continue

            tool_results = []
            for tu in tool_uses:
                await emit("tool", name=tu["name"], input=tu["input"])
                if tu["name"] == "run_command" and self._has_sandbox:
                    ex = await self._try(
                        "sandbox",
                        "exec",
                        {"command": tu["input"]["command"], "timeout_ms": tu["input"].get("timeout_ms", 30000)},
                    )
                    if ex is None:
                        obs, is_err = "sandbox unavailable", True
                    else:
                        obs = f"exit={ex['exit_code']}\n{ex['stdout']}\n{ex['stderr']}"
                        is_err = ex["exit_code"] != 0
                else:
                    inv = await self._try("tools", "invoke", {"name": tu["name"], "input": tu["input"]})
                    if inv is None:
                        obs, is_err = f"no tools brick bound; cannot run {tu['name']}", True
                    else:
                        obs, is_err = inv.get("output", ""), bool(inv.get("is_error"))
                await emit("tool", name=tu["name"], output=obs[:2000], is_error=is_err)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": tu["id"], "content": obs[:8000], "is_error": is_err}
                )
            transcript.append({"role": "tool", "content": tool_results})

            if plan_id:
                done = await self._try("planner", "is_complete", {"plan_id": plan_id})
                if done and done.get("complete"):
                    status = "completed"
                    break

        else:
            status = "max_iterations"

        if not summary:
            summary = f"stopped after {iterations} iteration(s) with status {status}"

        # 5. remember -----------------------------------------------------------------
        await self._try(
            "memory",
            "write",
            {"content": f"goal: {goal}\noutcome: {status}\nsummary: {summary}", "tags": ["orchestrator-run"]},
        )
        # 6. record the assistant turn in local chat history (no-op if unbound)
        await self._opt(
            "conversation",
            "append",
            {"session_id": session_id, "message": {"role": "assistant", "content": summary}},
        )

        out: dict = {"status": status, "iterations": iterations, "summary": summary}
        if saw_usage:
            out["usage"] = {"input_tokens": in_tot, "output_tokens": out_tot}
        return out

    @rpc("orchestrator.compact")
    async def compact(self, params, ctx):
        """Compress the working context this orchestrator holds for a session.

        The default loop keeps no state between runs beyond the local ``conversation`` log, so
        compaction summarises the turns since the last compaction and appends the summary as one
        turn tagged ``metadata.kind = "compaction"``. Later ``run`` calls load only that summary
        and what follows it. Non-destructive: the verbatim turns stay in the log.
        """
        session_id = str(params.get("session_id") or self.config.get("session_id") or "default")

        history = await self._opt("conversation", "load", {"session_id": session_id})
        if not history or not history.get("messages"):
            return {"status": "noop", "turns_before": 0, "turns_after": 0}

        live = _after_last_compaction(history["messages"])
        if live and (live[0].get("metadata") or {}).get("kind") == "compaction":
            body = live[1:]  # already-compacted; only compress what came after the marker
        else:
            body = live
        if len(body) <= 2:
            return {"status": "noop", "turns_before": len(body), "turns_after": len(body)}

        convo = "\n\n".join(f"{e['message'].get('role', '?')}: {_turn_text(e['message'])}" for e in body)
        gen = await self.host.contract_call(
            "inference",
            "generate",
            {
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You compress conversations. Reply with a compact summary that keeps "
                            "decisions, file paths, and open threads so a later session can "
                            "continue. No preamble."
                        ),
                    },
                    {"role": "user", "content": convo},
                ],
                "max_tokens": 512,
            },
        )
        msg = gen["message"]
        blocks = msg["content"] if isinstance(msg["content"], list) else [
            {"type": "text", "text": msg["content"]}
        ]
        summary = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        summary = summary or "(summary unavailable)"

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
    run(OrchestratorDefault())
