"""Phase 3 / Milestone 1 criterion 5: crash isolation. The kernel process survives every fixture
brick failure, emits the crash event, and applies the restart policy."""

from __future__ import annotations

import asyncio

import pytest

from veridian.contracts.errors import ProtocolError
from veridian.kernel.events import (
    BRICK_CRASHED,
    BRICK_MALFORMED_LINE,
    BRICK_READY,
    BRICK_RESTART_EXHAUSTED,
    BRICK_RESTARTING,
)
from veridian.kernel import RestartPolicy
from tests.kernel.conftest import make_kernel


async def test_kernel_survives_brick_exiting_mid_request(kernel_factory):
    k = await kernel_factory("echo_crash.toml")
    try:
        with pytest.raises(ProtocolError):
            await k.call("echo", "say", {"text": "boom"}, timeout=5.0)

        # kernel is still alive and responsive for other work
        assert k.bound() == {"echo": "echo/crash-mid-request"}

        # let the crash handler and one restart cycle run
        await asyncio.sleep(1.5)

        crashes = [e for e in k.events.history() if e.type == BRICK_CRASHED]
        assert crashes, "expected a brick.crashed event"

        restarts = [e for e in k.events.history() if e.type == BRICK_RESTARTING]
        assert restarts
        # after a restart the brick is ready again
        readies = [e for e in k.events.history() if e.type == BRICK_READY]
        assert len(readies) >= 2
    finally:
        await k.stop()


async def test_restart_budget_is_exhausted_after_repeated_crashes(tmp_path):
    k = make_kernel(
        "echo_crash.toml", tmp_path, restart_policy=RestartPolicy(max_restarts=2, backoff_base=0.1, window_seconds=30)
    )
    await k.start()
    try:
        for _ in range(4):
            with pytest.raises(ProtocolError):
                await k.call("echo", "say", {"text": "boom"}, timeout=5.0)
            await asyncio.sleep(0.4)
        await asyncio.sleep(0.5)
        assert any(e.type == BRICK_RESTART_EXHAUSTED for e in k.events.history())
    finally:
        await k.stop()


async def test_kernel_survives_garbage_on_stdout(tmp_path):
    # Reuse the plugin fixture directly via a one-off stack.
    stack = tmp_path / "s.toml"
    stack.write_text(
        '[stack]\nname="g"\n[bindings]\necho = "tests/fixtures/echo/garbage_then_recovers"\n',
        encoding="utf-8",
    )
    from veridian.kernel import Kernel, load_stack

    k = Kernel(load_stack(stack), workspace_root=tmp_path)
    await k.start()
    try:
        result = await k.call("echo", "say", {"text": "ok"}, timeout=5.0)
        assert result == {"text": "ok"}
        assert any(e.type == BRICK_MALFORMED_LINE for e in k.events.history())
    finally:
        await k.stop()


async def test_stop_shuts_every_brick_down_cleanly_no_crash(tmp_path):
    """Bug 2 regression. A multi-brick stack, started and then stopped: every brick process must
    exit on the *stop* path (returncode 0), with no ``brick.crashed`` event and no restart. The
    live failure was four brick processes access-violating (0xC0000005 / exit 3221225477) at
    once during teardown because a console Ctrl-C reached them directly; with bricks spawned in
    their own signal group, only the kernel's ordered shutdown ends them."""
    stack = tmp_path / "s.toml"
    stack.write_text(
        "[stack]\n"
        'name = "quartet"\n'
        "[policy]\n"
        'grant = ["process:spawn", "workspace:read", "workspace:write", "contract:context",\n'
        '         "contract:tools", "contract:memory", "contract:conversation"]\n'
        "[bindings]\n"
        'context = "pre-installed/bricks/context"\n'
        'tools = "pre-installed/bricks/toolkit"\n'
        'memory = "bricks/memory/ephemeral"\n'
        'conversation = { brick = "bricks/conversation/sqlite", config = { in_memory = true } }\n',
        encoding="utf-8",
    )
    from veridian.kernel import Kernel, load_stack

    k = Kernel(load_stack(stack), workspace_root=tmp_path)
    await k.start()
    supervisors = list(k._supervisors)
    assert len(supervisors) == 4

    await k.stop()
    await asyncio.sleep(0.2)  # let any (unwanted) crash handler fire

    crashes = [e for e in k.events.history() if e.type == BRICK_CRASHED]
    assert crashes == [], f"stop() crashed bricks: {[e.payload for e in crashes]}"
    assert not [e for e in k.events.history() if e.type == BRICK_RESTARTING]
    _ACCESS_VIOLATION = 3221225477  # 0xC0000005
    for s in supervisors:
        assert s._process is not None
        rc = s._process.returncode
        assert rc is not None, f"{s.name} did not exit on stop()"
        assert rc != _ACCESS_VIOLATION, f"{s.name} access-violated on shutdown (exit {rc})"


async def test_capability_liar_is_refused_at_start(tmp_path):
    stack = tmp_path / "s.toml"
    stack.write_text(
        '[stack]\nname="l"\n[bindings]\necho = "tests/fixtures/echo/capability_liar"\n',
        encoding="utf-8",
    )
    from veridian.contracts.errors import INVALID_MANIFEST
    from veridian.kernel import Kernel, load_stack

    k = Kernel(load_stack(stack), workspace_root=tmp_path)
    with pytest.raises(ProtocolError) as ei:
        await k.start()
    assert ei.value.code == INVALID_MANIFEST
    await k.stop()
