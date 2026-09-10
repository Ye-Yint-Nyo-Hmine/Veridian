"""Milestone 2 A4: ``$/cancel`` and its cascade through ``host.contract.call``.

Cancelling an outer request must not leave the work it triggered running detached. The kernel
routes ``host.contract.call``, so it sits on the parent→child edge and forwards the cancel down
the chain: cancelling ``echo.run`` cancels the ``echo_inner.wait`` call the kernel made on the
outer brick's behalf.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from veridian.contracts import PROTOCOL_VERSION, is_compatible_protocol
from veridian.contracts.errors import REQUEST_CANCELLED, ProtocolError
from veridian.kernel import Kernel, load_stack
from veridian.kernel.events import CONTRACT_CALL_CANCELLED

STACKS = Path(__file__).resolve().parents[1] / "fixtures" / "stacks"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


async def _wait_for(path: Path, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if path.exists():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_cancel_cascades_through_host_contract_call(tmp_path):
    k = Kernel(load_stack(STACKS / "cancel_cascade.toml"), workspace_root=tmp_path)
    await k.start()
    try:
        stream = await k.call_stream("echo", "run", {})

        # first delta => echo.run is live; then wait until the inner call is actually blocked
        async for _ in stream:
            break
        assert await _wait_for(tmp_path / "inner_started.marker"), "inner call never started"

        await stream.cancel()

        # the inner brick's handler task was cancelled...
        assert await _wait_for(tmp_path / "inner_cancelled.marker"), "cancel did not reach the inner brick"

        # ...and the stream itself now reports request_cancelled
        with pytest.raises(ProtocolError) as ei:
            await stream.result()
        assert ei.value.code == REQUEST_CANCELLED

        # the kernel recorded the cascade hop for the forwarded call
        assert any(
            e.type == CONTRACT_CALL_CANCELLED and e.payload.get("contract") == "echo_inner"
            for e in k.events.history()
        ), "kernel did not record the forwarded call being cancelled"
    finally:
        await k.stop()


async def test_protocol_1_0_brick_still_binds_against_a_1_1_kernel(tmp_path):
    stack = tmp_path / "s.toml"
    stack.write_text(
        '[stack]\nname="legacy"\n[policy]\ngrant=["contract:echo"]\n'
        '[bindings]\necho = "tests/fixtures/echo/legacy_1_0"\n',
        encoding="utf-8",
    )
    k = Kernel(load_stack(stack), workspace_root=tmp_path)
    await k.start()
    try:
        assert k.bound() == {"echo": "echo/legacy-1-0"}
        assert await k.call("echo", "say", {"text": "hi"}, timeout=5.0) == {"text": "hi"}
    finally:
        await k.stop()


def test_version_compat_is_major_only():
    assert is_compatible_protocol(PROTOCOL_VERSION)
    assert is_compatible_protocol("veridian/1.0")
    assert is_compatible_protocol("veridian/1.7")
    assert not is_compatible_protocol("veridian/2.0")
    assert not is_compatible_protocol("mcp/1.0")
    assert not is_compatible_protocol(None)
    assert not is_compatible_protocol("garbage")
