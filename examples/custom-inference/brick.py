"""example/retry-inference — a custom inference *engine*.

This is the point of splitting `inference` from `model_provider`: a provider adapts one API, an
inference engine is a strategy. This engine does the simplest possible strategy — call the bound
``model_provider`` and retry once with a nudge if the first attempt errors — but the same shape
scales to routing, ensembling, or tree search, none of which the kernel needs to know about.

Bind it with:

    [bindings]
    inference = "examples/custom-inference"
    model_provider = "bricks/inference/openai"   # or any model_provider brick
"""

from __future__ import annotations

from veridian.sdk import Brick, BrickError, rpc, run


class RetryInference(Brick):
    name = "example/retry-inference"
    version = "0.1.0"
    implements = {"inference": ["generate", "generate_stream", "models"]}

    async def _complete(self, params: dict) -> dict:
        try:
            return await self.host.contract_call("model_provider", "complete", params)
        except Exception as first:  # noqa: BLE001
            await self.host.log(f"provider failed ({first}); retrying once", level="warning")
            nudged = dict(params)
            nudged["messages"] = [
                *params["messages"],
                {"role": "user", "content": "(the previous attempt failed; please answer concisely)"},
            ]
            try:
                return await self.host.contract_call("model_provider", "complete", nudged)
            except Exception as second:  # noqa: BLE001
                raise BrickError(f"model_provider failed twice: {second}") from second

    @rpc("inference.generate")
    async def generate(self, params, ctx):
        return await self._complete(params)

    @rpc("inference.generate_stream", streaming=True)
    async def generate_stream(self, params, ctx):
        result = await self._complete(params)
        text = "".join(
            b.get("text", "") for b in result["message"]["content"] if isinstance(b, dict)
        )
        await ctx.emit_delta({"type": "text", "text": text})
        await ctx.emit_delta({"type": "stop", "stop_reason": result["stop_reason"]})
        return result

    @rpc("inference.models")
    async def models(self, params, ctx):
        return {"models": [{"id": "delegated-to-model_provider", "description": "whatever the bound provider offers"}]}


if __name__ == "__main__":
    run(RetryInference())
