"""Phase 4: the TypeScript SDK serves a brick that the Python kernel drives unchanged."""

from __future__ import annotations

import shutil

import pytest

from veridian.kernel import Kernel, load_stack
from tests.kernel.conftest import STACKS

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


async def _kernel(tmp_path) -> Kernel:
    k = Kernel(load_stack(STACKS / "echo_ts.toml"), workspace_root=tmp_path)
    await k.start()
    return k


async def test_ts_brick_handshake_and_call(tmp_path):
    k = await _kernel(tmp_path)
    try:
        assert k.bound() == {"echo": "echo/ts"}
        assert await k.call("echo", "say", {"text": "hi"}) == {"text": "hi"}
    finally:
        await k.stop()


async def test_ts_brick_streaming(tmp_path):
    k = await _kernel(tmp_path)
    try:
        stream = await k.call_stream("echo", "stream", {"text": "q", "chunks": 3})
        deltas = [d async for d in stream]
        assert [d["index"] for d in deltas] == [0, 1, 2]
        assert (await stream.result())["chunks"] == 3
    finally:
        await k.stop()


async def test_ts_brick_host_roundtrip(tmp_path):
    k = await _kernel(tmp_path)
    try:
        result = await k.call("echo", "roundtrip", {"text": "loop"})
        assert result == {"text": "loop", "via": "ts-host"}
    finally:
        await k.stop()
