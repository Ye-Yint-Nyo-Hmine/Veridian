"""Phase 2: brick subprocess supervision against the echo fixture bricks."""

from __future__ import annotations

import asyncio
import sys

import pytest

from veridian.contracts.errors import TIMEOUT, ProtocolError
from veridian.plugin_runtime import process as _process
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


# -- Bug 2: a console Ctrl-C must not be delivered straight to every brick -----------------------
#
# On Windows a console CTRL_C_EVENT goes to *every* process attached to the console at once. A
# brick that gets it unwinds `asyncio.run` and the interpreter finalises while the SDK's daemon
# stdin-reader thread is still parked in a blocking read -- which finalisation races into a
# 0xC0000005 access violation. Four bricks faulted together in the live session for exactly this.
# The fix: spawn each brick detached from the console's / foreground group's signal, so Ctrl-C
# reaches only the kernel, which then stops each brick in order.


async def test_brick_is_spawned_detached_from_the_consoles_ctrl_c(tmp_path, monkeypatch):
    seen: dict = {}
    real = asyncio.create_subprocess_exec

    async def _spy(*args, **kw):
        seen.update(kw)
        return await real(*args, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _spy)

    p = _proc("ok", tmp_path)
    ep = await p.start()
    try:
        await _init(ep)
        if sys.platform == "win32":
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            assert seen.get("creationflags", 0) & CREATE_NEW_PROCESS_GROUP
        else:
            assert seen.get("start_new_session") is True
    finally:
        await p.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process-group check")
async def test_posix_brick_runs_in_its_own_session_so_the_parents_sigint_misses_it(tmp_path):
    """`start_new_session` puts the brick in its own session/group. A SIGINT delivered to the
    kernel's foreground group (a console Ctrl-C) therefore never reaches the brick; the kernel
    alone decides when it stops, and `stop()` ends it cleanly (rc 0)."""
    import os

    p = _proc("ok", tmp_path)
    ep = await p.start()
    try:
        await _init(ep)
        assert os.getsid(p.pid) == p.pid                 # brick is its own session leader
        assert os.getpgid(p.pid) != os.getpgid(0)        # ... and not in the test runner's group
        # signalling our own group must not reach the brick
        os.killpg(os.getpgid(0), 0)
        assert p.alive
        assert (await ep.call("echo.say", {"text": "still here"}, timeout=5.0)) == {"text": "still here"}
    finally:
        rc = await p.stop()
    assert rc == 0


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


# -- egress teardown on the shutdown path (Milestone 2 / A3) -------------------------------------
#
# The regression these guard: `stop()` used to fire the egress proxy / network teardown off an
# un-awaited exit-watcher task with no timeout on its `docker` calls. When the loop then closed,
# a teardown mid-`communicate()` on an `--internal` network that still had an endpoint wedged
# shutdown for good and leaked both networks. Teardown must now be time-boxed, retried, ordered,
# and finished before `stop()` returns -- with no container engine anywhere near these tests.

_FAKE_POST_RUN = (
    ("docker", "rm", "-f", "veridian-egress-proxy-abcdef"),
    ("docker", "network", "rm", "veridian-egress-int-abcdef"),
    ("docker", "network", "rm", "veridian-egress-ext-abcdef"),
)


async def test_engine_command_is_time_boxed(monkeypatch):
    """A `docker` invocation that never returns is killed and reported, not waited on forever."""

    class _HangingProc:
        returncode = None

        def __init__(self):
            self.killed = False

        async def communicate(self):
            if self.killed:
                return b"", b""
            await asyncio.sleep(3600)

        def kill(self):
            self.killed = True
            self.returncode = -9

    hung = _HangingProc()

    async def _fake_exec(*_a, **_kw):
        return hung

    monkeypatch.setattr(_process.asyncio, "create_subprocess_exec", _fake_exec)

    rc, err = await asyncio.wait_for(
        _process._engine_command(("docker", "network", "rm", "x"), timeout=0.5), 10
    )
    assert rc == 124
    assert "timed out" in err
    assert hung.killed is True


async def test_stop_completes_even_when_egress_teardown_keeps_failing(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def _always_active_endpoints(cmd, *, timeout=None):
        calls.append(cmd)
        return 1, "Error response from daemon: network ... has active endpoints"

    monkeypatch.setattr(_process, "_engine_command", _always_active_endpoints)
    monkeypatch.setattr(_process, "_TEARDOWN_TOTAL_BUDGET", 8.0)

    p = _proc("ok", tmp_path, post_run=_FAKE_POST_RUN)
    ep = await p.start()
    await _init(ep)

    await asyncio.wait_for(p.stop(), 30)  # the point: it returns at all, and well inside budget

    attempted = {c for c in calls}
    assert attempted == set(_FAKE_POST_RUN)          # every command was tried
    assert len(calls) > len(_FAKE_POST_RUN)          # and retried, not tried once
    assert not p.alive


async def test_stop_teardown_is_ordered_and_rides_out_transient_failure(tmp_path, monkeypatch):
    calls: list[tuple[str, ...]] = []
    transient_left = {("docker", "network", "rm", "veridian-egress-int-abcdef"): 2}

    async def _clears_after_a_beat(cmd, *, timeout=None):
        calls.append(cmd)
        if transient_left.get(cmd, 0) > 0:
            transient_left[cmd] -= 1
            return 1, "has active endpoints"
        return 0, ""

    monkeypatch.setattr(_process, "_engine_command", _clears_after_a_beat)

    p = _proc("ok", tmp_path, post_run=_FAKE_POST_RUN)
    ep = await p.start()
    await _init(ep)

    await asyncio.wait_for(p.stop(), 20)

    # container removed before its networks; internal network retried until it detached
    assert calls[0] == _FAKE_POST_RUN[0]
    assert calls.index(_FAKE_POST_RUN[1]) < calls.index(_FAKE_POST_RUN[2])
    assert calls.count(("docker", "network", "rm", "veridian-egress-int-abcdef")) == 3
    assert not p.alive
