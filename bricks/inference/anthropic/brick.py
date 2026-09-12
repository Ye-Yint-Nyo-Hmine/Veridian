"""inference/anthropic — the Anthropic Messages API.

Uses the official ``anthropic`` Python SDK (``AsyncAnthropic``). Real provider only: no key means
``generate`` returns a clear error, never fabricated output.

Model ids and Messages API parameters here follow the bundled ``claude-api`` skill, not memory:
default model ``claude-opus-5``; ``max_tokens`` is required; ``system`` is a top-level parameter,
not a message role; assistant prefill is not used. Adaptive thinking is opt-in via config
(``thinking = "adaptive"`` plus an optional ``effort``), because a general inference brick should
behave like a plain completion unless a stack asks for more.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import _common  # noqa: E402

from veridian.sdk import Brick, BrickError, rpc, run  # noqa: E402

_DEFAULT_MODEL = "claude-opus-5"


class AnthropicInference(Brick):
    name = "inference/anthropic"
    version = "0.1.0"
    implements = {"inference": ["generate", "generate_stream", "models"]}

    async def on_initialize(self) -> bool:
        self.model = self.config.get("model", _DEFAULT_MODEL)
        self.max_tokens = int(self.config.get("max_tokens", 4096))
        self.thinking = self.config.get("thinking")  # None | "adaptive"
        self.effort = self.config.get("effort")  # None | low..max
        # Which environment variable carries the key; a stack may name a different one.
        self.key_env = self.config.get("api_key_env") or "ANTHROPIC_API_KEY"
        self.api_key = os.environ.get(self.key_env) or self.config.get("api_key")
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL") or self.config.get("base_url")
        self._client = None
        return True

    def _need_client(self):
        if self._client is not None:
            return self._client
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise BrickError("anthropic SDK not installed (uv sync --extra providers)", code=-32004) from exc
        if not self.api_key and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            raise BrickError(f"no {self.key_env} configured", code=-32004)
        kwargs = {}
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = AsyncAnthropic(**kwargs)
        return self._client

    def _request_kwargs(self, params: dict) -> dict:
        system, rest = _common.split_system(params["messages"])
        kwargs: dict = {
            "model": params.get("model") or self.model,
            "max_tokens": params.get("max_tokens", self.max_tokens),
            "messages": _common.to_anthropic_messages(rest),
        }
        if system:
            kwargs["system"] = system
        if "temperature" in params and not self.thinking:
            kwargs["temperature"] = params["temperature"]
        if params.get("stop"):
            kwargs["stop_sequences"] = params["stop"]
        tools = _common.to_anthropic_tools(params.get("tools"))
        if tools:
            kwargs["tools"] = tools
        if self.thinking == "adaptive":
            kwargs["thinking"] = {"type": "adaptive"}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        return kwargs

    @rpc("inference.generate")
    async def generate(self, params, ctx):
        client = self._need_client()
        msg = await client.messages.create(**self._request_kwargs(params))
        return _common.from_anthropic_message(msg)

    @rpc("inference.generate_stream", streaming=True)
    async def generate_stream(self, params, ctx):
        client = self._need_client()
        async with client.messages.stream(**self._request_kwargs(params)) as stream:
            async for text in stream.text_stream:
                await ctx.emit_delta({"type": "text", "text": text})
            final = await stream.get_final_message()
        result = _common.from_anthropic_message(final)
        await ctx.emit_delta({"type": "stop", "stop_reason": result["stop_reason"]})
        return result

    @rpc("inference.models")
    async def models(self, params, ctx):
        try:
            client = self._need_client()
            listed = await client.models.list()
            return {"models": [{"id": m.id, "description": getattr(m, "display_name", "")} for m in listed.data]}
        except Exception:  # noqa: BLE001 - no key configured
            return {"models": [{"id": self.model}]}


if __name__ == "__main__":
    run(AnthropicInference())
