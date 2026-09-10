"""model_provider/gateway — the OpenAI-compatible adapter pointed at a Veridian gateway.

This is deliberately almost nothing: the `inference/openai` implementation, with the base URL set
to the gateway and a bearer token attached. The gateway's job — authenticate, rate limit, route,
proxy, persist nothing — is a network service that ships as its own project (`veridian-gateway`)
and never lands in this repository, for the same reason the marketplace does not: a privacy claim
about a service nobody can inspect is worthless, so the service must be auditable on its own.

What core needs is only the client side, and this is it.
"""

from __future__ import annotations

import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[2] / "inference"))  # for _common
sys.path.insert(0, str(_HERE.parents[2] / "inference" / "openai"))

import brick as openai_brick  # noqa: E402

from veridian.sdk import BrickError, run  # noqa: E402

_DEFAULT_URL = "http://localhost:8080/v1"


class GatewayProvider(openai_brick.OpenAIInference):
    name = "model-provider/gateway"
    version = "0.1.0"
    implements = {"model_provider": ["complete", "embed"]}

    async def on_initialize(self) -> bool:
        await super().on_initialize()
        self.base_url = (
            self.config.get("base_url") or os.environ.get("VERIDIAN_GATEWAY_URL") or _DEFAULT_URL
        )
        # The auth header: AsyncOpenAI turns api_key into `Authorization: Bearer <token>`.
        self.api_key = os.environ.get("VERIDIAN_GATEWAY_TOKEN") or self.config.get("token")
        self.model = self.config.get("model", "gateway-default")
        self.embed_model = self.config.get("embed_model", "gateway-embed")
        return True

    def _need_client(self):
        if not self.api_key:
            raise BrickError("no VERIDIAN_GATEWAY_TOKEN configured", code=-32004)
        return super()._need_client()


if __name__ == "__main__":
    run(GatewayProvider())
