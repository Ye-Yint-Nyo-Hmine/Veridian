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


class OrchestratorDefault(Brick):
    name = "orchestrator/default"
    version = "0.1.0"
    implements = {"orchestrator": ["run"]}

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

        # 3. tool catalogue -------------------------------------------------------------
        tool_listing = await self._try("tools", "list", {})
        tool_schemas = list(tool_listing["tools"]) if tool_listing else []
        # If a sandbox is bound, offer a run_command tool backed by sandbox.exec, so an agent can
        # both edit files (tools brick) and run commands in the same loop.
        sandbox_probe = await self._try("sandbox", "reset", {})
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

        # prior turns from local chat history, if a `conversation` brick is bound
        history = await self._opt("conversation", "load", {"session_id": session_id, "limit": 40})
        if history and history.get("messages"):
            transcript.extend(e["message"] for e in history["messages"])
            await emit("log", message=f"loaded {len(history['messages'])} prior turn(s) from session {session_id!r}")

        transcript.append({"role": "user", "content": goal})
        # persist the user turn now, so it survives even a run that fails at inference
        await self._opt(
            "conversation", "append", {"session_id": session_id, "message": {"role": "user", "content": goal}}
        )

        iterations = 0
        status = "failed"
        summary = ""

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

        return {"status": status, "iterations": iterations, "summary": summary}


if __name__ == "__main__":
    run(OrchestratorDefault())
