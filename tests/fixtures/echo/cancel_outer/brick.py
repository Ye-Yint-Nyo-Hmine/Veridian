"""Outer brick for the cancellation-cascade test.

``echo.run`` is streaming: it emits one delta so the test can see the call is live, then makes a
``host.contract.call`` to ``echo_inner.wait`` and blocks there. Cancelling ``echo.run`` must
unwind this handler, which cancels the in-flight host call, which the kernel turns into a
``$/cancel`` to the inner brick.
"""

from veridian.sdk import Brick, rpc, run


class CancelOuter(Brick):
    name = "echo/cancel-outer"
    version = "1.0.0"
    implements = {"echo": ["run"]}

    @rpc("echo.run", streaming=True)
    async def run(self, params, ctx):
        await ctx.emit_delta({"event": "started"})
        out = await self.host.contract_call("echo_inner", "wait", {})
        return {"inner": out}


if __name__ == "__main__":
    run(CancelOuter())
