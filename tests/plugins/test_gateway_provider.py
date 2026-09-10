"""Milestone 2 / B4: the gateway-facing model_provider brick (client side only).

The gateway service is a separate repository. What core carries is one brick: the
OpenAI-compatible adapter with the gateway's base URL and a bearer token. This checks it is wired
correctly and that a stack can run inference through it without naming a model provider anywhere.
"""

from __future__ import annotations

import textwrap
import tomllib
from pathlib import Path

import pytest

from veridian.contracts.errors import ProtocolError
from veridian.kernel import Kernel, load_stack
from veridian.plugin_runtime.manifest import load_manifest

REPO = Path(__file__).resolve().parents[2]
GATEWAY_BRICK = REPO / "bricks" / "model_provider" / "gateway"


async def _kernel(tmp_path, body: str) -> Kernel:
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path)
    await k.start()
    return k


def test_manifest_is_a_plain_network_client():
    m = load_manifest(GATEWAY_BRICK)
    assert m.declares("model_provider")
    assert m.requires == ["network"]
    assert set(m.env_passthrough) == {"VERIDIAN_GATEWAY_URL", "VERIDIAN_GATEWAY_TOKEN"}


async def test_complete_errors_cleanly_without_a_token(tmp_path, monkeypatch):
    for var in ("VERIDIAN_GATEWAY_URL", "VERIDIAN_GATEWAY_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "gw"
        [policy]
        grant = ["network"]
        [bindings]
        model_provider = "bricks/model_provider/gateway"
        """,
    )
    try:
        with pytest.raises(ProtocolError) as ei:
            await k.call(
                "model_provider",
                "complete",
                {"messages": [{"role": "user", "content": "hi"}], "model": "x", "max_tokens": 8},
            )
        assert "GATEWAY_TOKEN" in str(ei.value)
    finally:
        await k.stop()


def test_gateway_stack_names_no_model_provider():
    cfg = tomllib.loads((REPO / "stacks" / "gateway.toml").read_text(encoding="utf-8"))
    bindings = cfg["bindings"]
    # inference is an engine that composes the model_provider contract; the provider binding is
    # the gateway client. No vendor brick, name, host, or model id appears in any binding value.
    assert bindings["model_provider"] == "bricks/model_provider/gateway"
    values = " ".join(str(v) for v in bindings.values()).lower()
    for vendor in ("anthropic", "inference/openai", "inference/local", "api.anthropic.com", "api.openai.com", "claude-", "gpt-"):
        assert vendor not in values, f"gateway stack binding names a provider: {vendor!r}"


async def test_gateway_stack_starts_and_binds_the_full_agent_loop(tmp_path):
    k = Kernel(load_stack(REPO / "stacks" / "gateway.toml"), workspace_root=tmp_path)
    await k.start()
    try:
        assert {"inference", "model_provider", "orchestrator", "conversation"} <= set(k.bound())
    finally:
        await k.stop()
