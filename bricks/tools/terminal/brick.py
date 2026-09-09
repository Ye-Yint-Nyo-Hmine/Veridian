"""tools/terminal — exposes a shell as a tool, executed through the sandbox contract.

This brick holds no execution logic of its own. It calls ``host.contract.call("sandbox", "exec")``,
so whichever sandbox brick the stack binds is what actually runs the command.
"""

from __future__ import annotations

from veridian.sdk import Brick, rpc, run

_TOOLS = [
    {
        "name": "run_command",
        "description": "Run a shell command in the workspace via the sandbox. Returns exit code and output.",
        "input_schema": {
            "type": "object",
            "required": ["command"],
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
                "timeout_ms": {"type": "integer"},
            },
        },
    }
]


class TerminalTools(Brick):
    name = "tools/terminal"
    version = "0.1.0"
    implements = {"tools": ["list", "invoke"]}

    @rpc("tools.list")
    async def list_(self, params, ctx):
        return {"tools": _TOOLS}

    @rpc("tools.invoke")
    async def invoke(self, params, ctx):
        if params["name"] != "run_command":
            return {"output": f"unknown tool {params['name']!r}", "is_error": True}
        inp = params.get("input", {})
        call: dict = {"command": inp["command"]}
        if "cwd" in inp:
            call["cwd"] = inp["cwd"]
        if "timeout_ms" in inp:
            call["timeout_ms"] = inp["timeout_ms"]
        try:
            res = await self.host.contract_call("sandbox", "exec", call)
        except Exception as exc:  # noqa: BLE001 - report, don't crash the agent loop
            return {"output": f"sandbox exec failed: {exc}", "is_error": True}
        body = (
            f"$ {inp['command']}\n"
            f"exit={res['exit_code']} timed_out={res['timed_out']}\n"
            f"--- stdout ---\n{res['stdout']}\n--- stderr ---\n{res['stderr']}"
        )
        return {"output": body, "is_error": res["exit_code"] != 0}


if __name__ == "__main__":
    run(TerminalTools())
