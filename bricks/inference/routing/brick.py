"""inference/routing — choose one real provider per request, by rule.

The stack binds a single ``inference`` brick, this one. Its config lists providers (each ``openai``
or ``anthropic`` kind, with its own model) and ordered rules. Each ``generate`` picks the first
matching rule's provider, or the default, and calls that provider for real. No fabricated output
anywhere — a request routed to a provider with no key returns that provider's error.

Rule keys (all optional, ANDed within a rule):
  when_max_messages  : int   -> matches when len(messages) <= N
  when_min_messages  : int   -> matches when len(messages) >= N
  when_has_tools     : bool  -> matches when the request carries tool schemas
  when_model_hint    : str   -> matches when params.model == this string (a symbolic hint)
"""

from __future__ import annotations

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import _common  # noqa: E402

from veridian.sdk import Brick, BrickError, rpc, run  # noqa: E402


class RoutingInference(Brick):
    name = "inference/routing"
    version = "0.1.0"
    implements = {"inference": ["generate", "generate_stream", "models"]}

    async def on_initialize(self) -> bool:
        self.providers = {p["name"]: p for p in self.config.get("providers", [])}
        self.rules = self.config.get("rules", [])
        self.default = self.config.get("default") or (next(iter(self.providers), None))
        if not self.providers:
            await self.host.log("routing brick has no providers configured", level="warning")
        self._clients: dict[str, object] = {}
        return True

    def _pick(self, params: dict) -> dict:
        n = len(params.get("messages", []))
        has_tools = bool(params.get("tools"))
        hint = params.get("model")
        for rule in self.rules:
            if "when_max_messages" in rule and n > rule["when_max_messages"]:
                continue
            if "when_min_messages" in rule and n < rule["when_min_messages"]:
                continue
            if "when_has_tools" in rule and bool(rule["when_has_tools"]) != has_tools:
                continue
            if "when_model_hint" in rule and rule["when_model_hint"] != hint:
                continue
            name = rule["use"]
            if name in self.providers:
                return self.providers[name]
        if self.default in self.providers:
            return self.providers[self.default]
        raise BrickError("no provider matched and no usable default", code=-32004)

    def _client_for(self, provider: dict):
        key = provider["name"]
        if key in self._clients:
            return self._clients[key]
        kind = provider["kind"]
        if kind == "anthropic":
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover
                raise BrickError("anthropic SDK not installed", code=-32004) from exc
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
                raise BrickError(f"provider {key!r}: no ANTHROPIC_API_KEY", code=-32004)
            client = AsyncAnthropic()
        elif kind == "openai":
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover
                raise BrickError("openai SDK not installed", code=-32004) from exc
            base_url = provider.get("base_url") or os.environ.get("OPENAI_BASE_URL")
            if not os.environ.get("OPENAI_API_KEY") and not base_url:
                raise BrickError(f"provider {key!r}: no OPENAI_API_KEY and no base_url", code=-32004)
            client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY") or "not-needed", base_url=base_url)
        else:
            raise BrickError(f"provider {key!r}: unknown kind {kind!r}", code=-32004)
        self._clients[key] = client
        return client

    async def _generate(self, provider: dict, params: dict) -> dict:
        client = self._client_for(provider)
        model = provider.get("model")
        if provider["kind"] == "anthropic":
            system, rest = _common.split_system(params["messages"])
            kwargs = {
                "model": model,
                "max_tokens": params.get("max_tokens", 4096),
                "messages": _common.to_anthropic_messages(rest),
            }
            if system:
                kwargs["system"] = system
            if _common.to_anthropic_tools(params.get("tools")):
                kwargs["tools"] = _common.to_anthropic_tools(params["tools"])
            msg = await client.messages.create(**kwargs)
            out = _common.from_anthropic_message(msg)
        else:
            kwargs = {
                "model": model,
                "messages": _common.to_openai_messages(params["messages"]),
                "max_tokens": params.get("max_tokens", 1024),
            }
            if _common.to_openai_tools(params.get("tools")):
                kwargs["tools"] = _common.to_openai_tools(params["tools"])
            resp = await client.chat.completions.create(**kwargs)
            out = _common.from_openai_choice(resp.choices[0], resp.model, resp.usage)
        out["routed_via"] = provider["name"]
        return out

    @rpc("inference.generate")
    async def generate(self, params, ctx):
        provider = self._pick(params)
        await self.host.log(f"routing -> {provider['name']} ({provider['kind']}:{provider.get('model')})")
        result = await self._generate(provider, params)
        result.pop("routed_via", None)
        return result

    @rpc("inference.generate_stream", streaming=True)
    async def generate_stream(self, params, ctx):
        # Route, then produce the full result and emit it as a single delta. A routing engine's
        # value is the decision, not token-level streaming from every backend.
        provider = self._pick(params)
        result = await self._generate(provider, params)
        result.pop("routed_via", None)
        text = _common.blocks_to_text(result["message"]["content"])
        await ctx.emit_delta({"type": "text", "text": text})
        await ctx.emit_delta({"type": "stop", "stop_reason": result["stop_reason"]})
        return result

    @rpc("inference.models")
    async def models(self, params, ctx):
        return {
            "models": [
                {"id": p.get("model", p["name"]), "description": f"{p['name']} ({p['kind']})"}
                for p in self.providers.values()
            ]
        }


if __name__ == "__main__":
    run(RoutingInference())
