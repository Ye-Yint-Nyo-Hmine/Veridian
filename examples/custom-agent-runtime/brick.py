"""example/linear-agent — a different agent architecture in ~60 lines.

The default orchestrator interleaves planning and acting. This one plans once up front, then walks
the plan straight through: one inference call per step, tools dispatched, no re-planning, no
completion check. It is neither better nor worse — it is *different*, and swapping it in is one
line:

    [bindings]
    orchestrator = "examples/custom-agent-runtime"

That line changes the entire agent loop without touching the kernel, the protocol, or any other
brick. That is the claim Veridian exists to make true.
"""

from __future__ import annotations

from veridian.sdk import Brick, rpc, run

_CONTRACT_NOT_BOUND = -32002


class LinearAgent(Brick):
    name = "example/linear-agent"
    version = "0.1.0"
    implements = {"orchestrator": ["run"]}

    async def _opt(self, contract, method, params):
        try:
            return await self.host.contract_call(contract, method, params)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) == _CONTRACT_NOT_BOUND:
                return None
            raise

    @rpc("orchestrator.run", streaming=True)
    async def run_(self, params, ctx):
        goal = params["goal"]

        ctx_hit = await self._opt("context", "retrieve", {"query": goal, "k": 4})
        context_text = "\n".join(c["text"] for c in (ctx_hit or {}).get("chunks", [])[:4])

        plan = await self._opt("planner", "plan", {"goal": goal})
        steps = [s["description"] for s in plan["steps"]] if plan else [goal]
        await ctx.emit_delta({"event": "step", "data": {"kind": "plan", "steps": steps}})

        tool_listing = await self._opt("tools", "list", {})
        tools = tool_listing["tools"] if tool_listing else []

        history = [
            {"role": "system", "content": f"You are an agent. Context:\n{context_text}"},
            {"role": "user", "content": goal},
        ]
        done = 0
        for step in steps:
            await ctx.emit_delta({"event": "step", "data": {"kind": "active", "description": step}})
            history.append({"role": "user", "content": f"Do this step now: {step}"})
            gen = {"messages": history, "max_tokens": 800}
            if tools:
                gen["tools"] = tools
            result = await self.host.contract_call("inference", "generate", gen)
            msg = result["message"]
            history.append(msg)
            blocks = msg["content"] if isinstance(msg["content"], list) else [{"type": "text", "text": msg["content"]}]
            for tu in [b for b in blocks if b.get("type") == "tool_use"]:
                inv = await self._opt("tools", "invoke", {"name": tu["name"], "input": tu["input"]})
                obs = (inv or {}).get("output", "no tools bound")
                await ctx.emit_delta({"event": "tool", "data": {"name": tu["name"], "output": obs[:500]}})
                history.append(
                    {"role": "tool", "content": [{"type": "tool_result", "tool_use_id": tu["id"], "content": obs[:4000]}]}
                )
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            if text:
                await ctx.emit_delta({"event": "message", "data": {"text": text}})
            done += 1

        return {"status": "completed", "iterations": done, "summary": f"walked {done} step(s) linearly"}


if __name__ == "__main__":
    run(LinearAgent())
