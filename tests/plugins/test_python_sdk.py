"""Phase 4: the Python SDK serves a real brick over the wire, under the real kernel."""

from __future__ import annotations

import pytest

from veridian.contracts.errors import UNSUPPORTED_METHOD, ProtocolError
from veridian.kernel import Kernel, load_stack
from tests.kernel.conftest import STACKS


async def _kernel(tmp_path) -> Kernel:
    k = Kernel(load_stack(STACKS / "echo_sdk.toml"), workspace_root=tmp_path)
    await k.start()
    return k


async def test_sdk_brick_handshake_and_call(tmp_path):
    k = await _kernel(tmp_path)
    try:
        assert k.bound() == {"echo": "echo/sdk"}
        assert await k.call("echo", "say", {"text": "hi"}) == {"text": "hi"}
    finally:
        await k.stop()


async def test_sdk_brick_streaming(tmp_path):
    k = await _kernel(tmp_path)
    try:
        stream = await k.call_stream("echo", "stream", {"text": "q", "chunks": 3})
        deltas = [d async for d in stream]
        assert [d["index"] for d in deltas] == [0, 1, 2]
        assert (await stream.result())["chunks"] == 3
    finally:
        await k.stop()


async def test_sdk_host_proxy_roundtrip(tmp_path):
    k = await _kernel(tmp_path)
    try:
        result = await k.call("echo", "roundtrip", {"text": "loop"})
        assert result == {"text": "loop", "via": "sdk-host"}
        assert any(e.type == "brick.log" for e in k.events.history())
    finally:
        await k.stop()


async def test_sdk_unimplemented_method_maps_to_unsupported(tmp_path):
    k = await _kernel(tmp_path)
    try:
        with pytest.raises(ProtocolError) as ei:
            await k.call("echo", "nonexistent", {})
        assert ei.value.code == UNSUPPORTED_METHOD
    finally:
        await k.stop()
