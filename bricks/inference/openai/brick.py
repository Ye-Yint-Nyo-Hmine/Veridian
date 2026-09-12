"""inference/openai — OpenAI-compatible chat completions + embeddings.

Real provider only. There is no mock path: without a key (and, for a non-default base URL, a
reachable server) ``generate`` returns a clear error. ``models`` returns provider metadata, which
is not model output.
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import _common  # noqa: E402

from veridian.sdk import Brick, BrickError, rpc, run  # noqa: E402

_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_EMBED = "text-embedding-3-small"


class OpenAIInference(Brick):
    name = "inference/openai"
    version = "0.1.0"
    implements = {
        "inference": ["generate", "generate_stream", "models"],
        "model_provider": ["complete", "embed"],
    }

    async def on_initialize(self) -> bool:
        self.model = self.config.get("model", _DEFAULT_MODEL)
        self.embed_model = self.config.get("embed_model", _DEFAULT_EMBED)
        self.base_url = self.config.get("base_url") or os.environ.get("OPENAI_BASE_URL")
        # Which environment variable carries the key. Every OpenAI-compatible provider — Gemini,
        # DeepSeek, Moonshot — is this same adapter behind a different base URL, and each has its
        # own conventional variable name. A stack that points here names the one it means; nothing
        # falls back to OPENAI_API_KEY, so a key for one provider is never sent to another.
        self.key_env = self.config.get("api_key_env") or "OPENAI_API_KEY"
        self.api_key = os.environ.get(self.key_env) or self.config.get("api_key")
        self._client = None
        return True

    def _need_client(self):
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise BrickError("openai SDK not installed (uv sync --extra providers)", code=-32004) from exc
        if not self.api_key and not self.base_url:
            raise BrickError(f"no {self.key_env} and no base_url configured", code=-32004)
        self._client = AsyncOpenAI(api_key=self.api_key or "not-needed", base_url=self.base_url)
        return self._client

    async def _create(self, params, stream: bool):
        client = self._need_client()
        kwargs = {
            "model": params.get("model") or self.model,
            "messages": _common.to_openai_messages(params["messages"]),
            "max_tokens": params.get("max_tokens", 1024),
            "stream": stream,
        }
        if "temperature" in params:
            kwargs["temperature"] = params["temperature"]
        if params.get("stop"):
            kwargs["stop"] = params["stop"]
        tools = _common.to_openai_tools(params.get("tools"))
        if tools:
            kwargs["tools"] = tools
        return await client.chat.completions.create(**kwargs)

    @rpc("inference.generate")
    async def generate(self, params, ctx):
        resp = await self._create(params, stream=False)
        return _common.from_openai_choice(resp.choices[0], resp.model, resp.usage)

    @rpc("inference.generate_stream", streaming=True)
    async def generate_stream(self, params, ctx):
        client = self._need_client()
        kwargs = {
            "model": params.get("model") or self.model,
            "messages": _common.to_openai_messages(params["messages"]),
            "max_tokens": params.get("max_tokens", 1024),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if params.get("tools"):
            kwargs["tools"] = _common.to_openai_tools(params["tools"])

        text_parts: list[str] = []
        finish = "stop"
        usage = None
        model = kwargs["model"]
        async for chunk in await client.chat.completions.create(**kwargs):
            if chunk.usage:
                usage = chunk.usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                text_parts.append(delta.content)
                await ctx.emit_delta({"type": "text", "text": delta.content})
            if chunk.choices[0].finish_reason:
                finish = chunk.choices[0].finish_reason
        await ctx.emit_delta({"type": "stop", "stop_reason": _common._OPENAI_STOP.get(finish, "stop")})
        return {
            "message": {"role": "assistant", "content": [{"type": "text", "text": "".join(text_parts)}]},
            "model": model,
            "usage": {
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
            },
            "stop_reason": _common._OPENAI_STOP.get(finish, "stop"),
        }

    @rpc("inference.models")
    async def models(self, params, ctx):
        try:
            client = self._need_client()
            listed = await client.models.list()
            return {"models": [{"id": m.id} for m in listed.data][:100]}
        except Exception:  # noqa: BLE001 - no key, or a local server without /models
            return {"models": [{"id": self.model}, {"id": self.embed_model}]}

    # -- model_provider ---------------------------------------------------------------

    @rpc("model_provider.complete")
    async def complete(self, params, ctx):
        resp = await self._create(params, stream=False)
        return _common.from_openai_choice(resp.choices[0], resp.model, resp.usage)

    @rpc("model_provider.embed")
    async def embed(self, params, ctx):
        client = self._need_client()
        resp = await client.embeddings.create(
            model=params.get("model") or self.embed_model, input=params["input"]
        )
        return {
            "embeddings": [d.embedding for d in resp.data],
            "model": resp.model,
            "usage": {"input_tokens": getattr(resp.usage, "prompt_tokens", 0) or 0, "output_tokens": 0},
        }


if __name__ == "__main__":
    run(OpenAIInference())
