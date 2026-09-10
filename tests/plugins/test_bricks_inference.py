"""Phase 5: inference bricks.

Hermetic tests assert the bricks start, speak the protocol, and return *well-formed errors* when
no provider is configured — there is no mock inference anywhere. Real generation is covered by the
live-marked tests at the bottom, gated on a real key or local server.
"""

from __future__ import annotations

import os
import textwrap

import pytest

from tests.live_providers import LOCAL_MODEL, requires_local_server
from veridian.contracts.errors import ProtocolError
from veridian.kernel import Kernel, load_stack


async def _kernel(tmp_path, body: str) -> Kernel:
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path)
    await k.start()
    return k


@pytest.mark.parametrize("which", ["openai", "anthropic", "local", "routing"])
async def test_inference_models_is_wellformed(tmp_path, monkeypatch, which):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    if which == "routing":
        binding = (
            'inference = { brick = "bricks/inference/routing", '
            'config = { providers = [ { name = "p", kind = "openai", model = "gpt-4o-mini" } ] } }'
        )
    else:
        binding = f'inference = "bricks/inference/{which}"'
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings]
        {binding}
        """,
    )
    try:
        res = await k.call("inference", "models", {})
        assert isinstance(res["models"], list) and res["models"]
        assert all("id" in m for m in res["models"])
    finally:
        await k.stop()


@pytest.mark.parametrize("which", ["openai", "anthropic"])
async def test_inference_generate_errors_without_key(tmp_path, monkeypatch, which):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings]
        inference = "bricks/inference/{which}"
        """,
    )
    try:
        with pytest.raises(ProtocolError) as ei:
            await k.call("inference", "generate", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 16})
        assert ei.value.code != 0
        assert "KEY" in str(ei.value).upper() or "not installed" in str(ei.value)
    finally:
        await k.stop()


async def test_routing_picks_by_rule_but_still_needs_real_providers(tmp_path, monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings.inference]
        brick = "bricks/inference/routing"
        [bindings.inference.config]
        default = "big"
        providers = [
          { name = "big", kind = "anthropic", model = "claude-opus-5" },
          { name = "small", kind = "openai", model = "gpt-4o-mini" },
        ]
        rules = [ { when_max_messages = 1, use = "small" } ]
        """,
    )
    try:
        # one message -> rule picks the openai provider -> fails for lack of a key (not a mock)
        with pytest.raises(ProtocolError) as ei:
            await k.call("inference", "generate", {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 16})
        assert "small" in str(ei.value) or "OPENAI" in str(ei.value).upper()
    finally:
        await k.stop()


# --------------------------------------------------------------------------------------
# Live tests — deselected by default, run with `uv run pytest -m live`.
# --------------------------------------------------------------------------------------

live = pytest.mark.live


@live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
async def test_live_anthropic_generate(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings]
        inference = { brick = "bricks/inference/anthropic", config = { model = "claude-opus-5", max_tokens = 64 } }
        """,
    )
    try:
        res = await k.call(
            "inference",
            "generate",
            {"messages": [{"role": "user", "content": "Reply with exactly: pong"}], "max_tokens": 64},
        )
        text = "".join(b["text"] for b in res["message"]["content"] if b["type"] == "text")
        assert "pong" in text.lower()
        assert res["usage"]["output_tokens"] > 0
    finally:
        await k.stop()


@live
@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set")
async def test_live_anthropic_stream(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings]
        inference = "bricks/inference/anthropic"
        """,
    )
    try:
        stream = await k.call_stream(
            "inference",
            "generate_stream",
            {"messages": [{"role": "user", "content": "Count to five, one number per line."}], "max_tokens": 64},
        )
        deltas = [d async for d in stream]
        assert any(d["delta"]["type"] == "text" for d in deltas)
        final = await stream.result()
        assert final["stop_reason"] in {"stop", "length"}
    finally:
        await k.stop()


@live
@requires_local_server
async def test_live_local_generate(tmp_path):
    # A5: the direct path for bricks/inference/local — an OpenAI-compatible server on localhost
    # (Ollama by default). No cloud key involved.
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings]
        inference = {{ brick = "bricks/inference/local", config = {{ model = "{LOCAL_MODEL}" }} }}
        """,
    )
    try:
        res = await k.call(
            "inference",
            "generate",
            {"messages": [{"role": "user", "content": "Reply with the single word: pong"}], "max_tokens": 64},
        )
        text = "".join(b["text"] for b in res["message"]["content"] if b["type"] == "text")
        assert text.strip(), "local model returned no text"
        assert res["usage"]["output_tokens"] > 0
    finally:
        await k.stop()


@live
@requires_local_server
async def test_live_local_models_lists_the_server(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings]
        inference = "bricks/inference/local"
        """,
    )
    try:
        res = await k.call("inference", "models", {})
        assert isinstance(res["models"], list) and res["models"]
        assert all("id" in m for m in res["models"])
    finally:
        await k.stop()


@live
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
async def test_live_openai_generate_and_embed(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "i"
        [policy]
        grant = ["network"]
        [bindings]
        inference = "bricks/inference/openai"
        model_provider = "bricks/inference/openai"
        """,
    )
    try:
        gen = await k.call(
            "inference",
            "generate",
            {"messages": [{"role": "user", "content": "Reply with exactly: pong"}], "max_tokens": 16},
        )
        assert "pong" in "".join(
            b["text"] for b in gen["message"]["content"] if b["type"] == "text"
        ).lower()
        emb = await k.call("model_provider", "embed", {"input": ["hello", "world"], "model": "text-embedding-3-small"})
        assert len(emb["embeddings"]) == 2 and len(emb["embeddings"][0]) > 10
    finally:
        await k.stop()
