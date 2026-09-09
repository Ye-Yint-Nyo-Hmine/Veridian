"""Echo fixture implemented with the Python SDK — proves the SDK round-trips the wire contract."""

from veridian.sdk import Brick, rpc, run


class EchoBrick(Brick):
    name = "echo/sdk"
    version = "1.0.0"
    implements = {"echo": ["say", "stream", "roundtrip"]}

    @rpc("echo.say")
    async def say(self, params, ctx):
        return {"text": params.get("text", "")}

    @rpc("echo.stream", streaming=True)
    async def stream(self, params, ctx):
        text = params.get("text", "")
        n = int(params.get("chunks", 3))
        for i in range(n):
            await ctx.emit_delta({"index": i, "text": text})
        return {"text": text, "chunks": n}

    @rpc("echo.roundtrip")
    async def roundtrip(self, params, ctx):
        await self.host.log("calling echo.say through the host proxy")
        out = await self.host.contract_call("echo", "say", {"text": params.get("text", "")})
        return {"text": out["text"], "via": "sdk-host"}


if __name__ == "__main__":
    run(EchoBrick())
