"""Phase 4: the Python SDK serves a real brick over the wire, under the real kernel."""

from __future__ import annotations

import textwrap

import pytest

from veridian.contracts.errors import BRICK_INTERNAL_ERROR, UNSUPPORTED_METHOD, ProtocolError
from veridian.kernel import Kernel, load_stack
from tests.fixtures import ECHO_ROOT
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


async def test_handler_crash_is_surfaced_with_brick_name_and_traceback(tmp_path, monkeypatch):
    """Bug 3: a brick handler that raises must not reach the caller as a bare one-liner. The SDK
    attaches the brick name, method and full traceback to the error, and writes the traceback to
    ``VERIDIAN_HOME/logs/brick-errors.log`` so the failure is diagnosable after the fact."""
    home = tmp_path / "vhome"
    monkeypatch.setenv("VERIDIAN_HOME", str(home))

    stack = tmp_path / "s.toml"
    stack.write_text(
        textwrap.dedent(
            f"""
            [stack]
            name = "raises"
            [policy]
            grant = ["contract:echo"]
            [bindings]
            echo = "{(ECHO_ROOT / 'raises').as_posix()}"
            """
        ),
        encoding="utf-8",
    )
    k = Kernel(load_stack(stack), workspace_root=tmp_path)
    await k.start()
    try:
        with pytest.raises(ProtocolError) as ei:
            await k.call("echo", "say", {"text": "hi"})
        exc = ei.value
        assert exc.code == BRICK_INTERNAL_ERROR
        assert "TypeError" in exc.message
        data = exc.data or {}
        assert data.get("brick") == "echo/raises"
        assert data.get("method") == "echo.say"
        assert "Traceback (most recent call last)" in data.get("traceback", "")
        assert "brick.py" in data["traceback"] and "say" in data["traceback"]

        log = home / "logs" / "brick-errors.log"
        assert log.exists()
        body = log.read_text(encoding="utf-8")
        assert "echo/raises" in body and "echo.say" in body
        assert "TypeError" in body
    finally:
        await k.stop()
