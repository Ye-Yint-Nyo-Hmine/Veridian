"""Phase 2: brick subprocess supervision against the echo fixture bricks."""

from __future__ import annotations

import asyncio

import pytest

from veridian.contracts.errors import TIMEOUT, ProtocolError
from veridian.plugin_runtime.manifest import load_manifest
from veridian.plugin_runtime.process import BrickProcess, base_env
from tests.fixtures import ECHO_ROOT


def _proc(dirname: str, tmp_path, **kw) -> BrickProcess:
    manifest = load_manifest(ECHO_ROOT / dirname)
    return BrickProcess(
        manifest.name,
        manifest.resolved_command(),
        cwd=tmp_path,
        env=base_env(manifest.env_passthrough),
        **kw,
    )


async def _init(ep) -> dict:
    return await ep.call(
        "plugin.initialize",
        {"protocol_version": "veridian/1.0", "capabilities": [], "workspace_root": ".", "config": {}},
        timeout=5.0,
    )


async def test_echo_ok_full_handshake_and_call(tmp_path):
    p = _proc("ok", tmp_path)
    ep = await p.start()
    try:
        init = await _init(ep)
        assert init["ready"] is True
        assert init["brick"]["name"] == "echo/ok"
        caps = await ep.call("plugin.capabilities", {}, timeout=5.0)
        assert caps["contracts"][0]["methods"] == ["say", "stream"]
        said = await ep.call("echo.say", {"text": "ping"}, timeout=5.0)
        assert said == {"text": "ping"}
    finally:
        await p.stop()


async def test_echo_ok_streaming(tmp_path):
    p = _proc("ok", tmp_path)
    ep = await p.start()
    try:
        await _init(ep)
        stream = await ep.call_stream("echo.stream", {"text": "z", "chunks": 4}, timeout=5.0)
        deltas = [d async for d in stream]
        assert [d["index"] for d in deltas] == [0, 1, 2, 3]
        assert (await stream.result())["chunks"] == 4
    finally:
        await p.stop()


async def test_crash_mid_request_reports_unavailable_and_exit(tmp_path):
    p = _proc("crash_mid_request", tmp_path)
    ep = await p.start()
    try:
        await _init(ep)
        with pytest.raises(ProtocolError):
            await ep.call("echo.say", {"text": "boom"}, timeout=5.0)
        assert await p.wait() != 0
    finally:
        await p.stop()


async def test_hang_hits_call_timeout_then_stop_kills(tmp_path):
    p = _proc("hang", tmp_path, kill_timeout=2.0)
    ep = await p.start()
    try:
        await _init(ep)
        with pytest.raises(ProtocolError) as ei:
            await ep.call("echo.say", {"text": "x"}, timeout=0.3)
        assert ei.value.code == TIMEOUT
        assert p.alive
    finally:
        code = await p.stop()
    assert code is not None
    assert not p.alive


async def test_garbage_then_recovers_survives(tmp_path):
    malformed: list[str] = []
    p = _proc("garbage_then_recovers", tmp_path, on_malformed=malformed.append)
    ep = await p.start()
    try:
        await _init(ep)
        result = await ep.call("echo.say", {"text": "after garbage"}, timeout=5.0)
        assert result == {"text": "after garbage"}
        assert len(malformed) >= 3
    finally:
        await p.stop()


async def test_garbage_forever_trips_threshold(tmp_path):
    p = _proc("garbage_forever", tmp_path)
    ep = await p.start()
    try:
        await _init(ep)
        with pytest.raises(ProtocolError):
            await ep.call("echo.say", {"text": "x"}, timeout=5.0)
        await asyncio.sleep(0.1)
        assert ep.closed
    finally:
        await p.stop()


async def test_stderr_is_forwarded_not_parsed(tmp_path):
    lines: list[str] = []
    p = _proc("hang", tmp_path, on_stderr=lines.append, kill_timeout=2.0)
    ep = await p.start()
    try:
        await _init(ep)
        with pytest.raises(ProtocolError):
            await ep.call("echo.say", {"text": "x"}, timeout=0.3)
        await asyncio.sleep(0.1)
        assert any("hanging forever" in ln for ln in lines)
    finally:
        await p.stop()
