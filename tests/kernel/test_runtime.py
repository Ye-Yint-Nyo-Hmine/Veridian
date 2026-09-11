"""Phase 3: the kernel starts a stack, routes host.contract.call, and enforces permissions."""

from __future__ import annotations

import pytest

from veridian.contracts.errors import CONTRACT_NOT_BOUND, PERMISSION_DENIED, ProtocolError
from veridian.kernel.events import BRICK_READY, CONTRACT_CALL, KERNEL_READY, PERMISSION_DENIED as EV_DENIED
from tests.kernel.conftest import make_kernel


async def test_start_binds_and_calls(kernel_factory):
    k = await kernel_factory("echo_ok.toml")
    try:
        assert k.bound() == {"echo": "echo/ok"}
        assert [e.type for e in k.events.history("kernel.*")] == [KERNEL_READY]
        assert any(e.type == BRICK_READY for e in k.events.history("brick.*"))
        result = await k.call("echo", "say", {"text": "hello"})
        assert result == {"text": "hello"}
    finally:
        await k.stop()


async def test_unbound_contract_errors(kernel_factory):
    k = await kernel_factory("echo_ok.toml")
    try:
        with pytest.raises(ProtocolError) as ei:
            await k.call("memory", "read", {"id": "x"})
        assert ei.value.code == CONTRACT_NOT_BOUND
    finally:
        await k.stop()


async def test_host_contract_call_is_routed_through_the_kernel(kernel_factory):
    k = await kernel_factory("echo_roundtrip.toml")
    try:
        # echo.via_host makes the brick call host.contract.call -> kernel -> echo.say
        result = await k.call("echo", "via_host", {"text": "ping"})
        assert result == {"text": "ping", "via": "host"}
        assert any(e.type == CONTRACT_CALL and e.payload["method"] == "say" for e in k.events.history())
        # host.log from the brick surfaced on the bus
        assert any(e.type == "brick.log" for e in k.events.history())
    finally:
        await k.stop()


async def test_host_permission_request_granted_when_policy_allows(kernel_factory):
    k = await kernel_factory("echo_roundtrip.toml")
    try:
        result = await k.call("echo", "needs_perm", {"capability": "network"})
        assert result == {"granted": True}
    finally:
        await k.stop()


async def test_host_contract_call_denied_without_capability(kernel_factory):
    k = await kernel_factory("echo_roundtrip_noperm.toml")
    try:
        with pytest.raises(ProtocolError) as ei:
            await k.call("echo", "via_host", {"text": "ping"})
        # the brick's host_call raises RuntimeError on a host error -> surfaces as brick_internal_error
        assert ei.value.code != 0
        assert any(e.type == EV_DENIED for e in k.events.history())
    finally:
        await k.stop()


async def test_permission_request_denied_without_policy(kernel_factory):
    k = await kernel_factory("echo_roundtrip_noperm.toml")
    try:
        result = await k.call("echo", "needs_perm", {"capability": "network"})
        assert result == {"granted": False}
    finally:
        await k.stop()


async def test_restrict_capabilities_removes_a_granted_capability_live(kernel_factory):
    """The interactive session's mode is built on this: withholding a capability drops it from
    every brick's effective set *and* from the running supervisor, and it cannot be re-granted
    dynamically until the restriction is lifted."""
    k = await kernel_factory("echo_roundtrip.toml")
    try:
        sup = next(s for s in k._supervisors if s.name == "echo/roundtrip")
        assert "network" in k.effective_capabilities("echo/roundtrip")
        assert await k.call("echo", "needs_perm", {"capability": "network"}) == {"granted": True}

        k.restrict_capabilities({"network"})
        assert "network" not in k.effective_capabilities("echo/roundtrip")
        assert "network" not in sup.effective_capabilities
        assert "contract:echo" in k.effective_capabilities("echo/roundtrip")  # untouched
        # a dynamic re-grant of the withheld capability is refused
        assert await k.call("echo", "needs_perm", {"capability": "network"}) == {"granted": False}
        # a capability that is still held keeps working
        assert await k.call("echo", "via_host", {"text": "ping"}) == {"text": "ping", "via": "host"}

        k.restrict_capabilities([])  # 'auto' — nothing withheld
        assert "network" in k.effective_capabilities("echo/roundtrip")
        assert await k.call("echo", "needs_perm", {"capability": "network"}) == {"granted": True}
    finally:
        await k.stop()


async def test_restrict_capabilities_before_start_is_applied_as_bricks_come_up(tmp_path):
    from tests.kernel.conftest import make_kernel

    k = make_kernel("echo_roundtrip.toml", tmp_path)
    k.restrict_capabilities({"network"})
    await k.start()
    try:
        assert "network" not in k.effective_capabilities("echo/roundtrip")
        assert "contract:echo" in k.effective_capabilities("echo/roundtrip")
    finally:
        await k.stop()


async def test_rebind_swaps_the_live_brick_with_a_new_config(kernel_factory):
    """``/model`` rides on this: rebind restarts one contract's brick with merged config through a
    fresh supervisor, keeps exactly one supervisor for the contract, and leaves the contract
    callable."""
    k = await kernel_factory("echo_ok.toml")
    try:
        before = k.registry.resolve("echo")
        await k.rebind("echo", config={"greeting": "hi"})
        after = k.registry.resolve("echo")
        assert after is not before  # a fresh process/handle
        assert k.stack.binding_for("echo").config.get("greeting") == "hi"
        assert len([s for s in k._supervisors if s.contract == "echo"]) == 1
        assert await k.call("echo", "say", {"text": "ping"}) == {"text": "ping"}
    finally:
        await k.stop()


async def test_rebind_failure_leaves_the_previous_brick_bound_and_working(kernel_factory, monkeypatch):
    k = await kernel_factory("echo_ok.toml")
    try:
        original = k.registry.resolve("echo")

        from veridian.kernel import lifecycle

        async def _wont_start(self):
            raise lifecycle.BrickStartError("planted failure")

        monkeypatch.setattr(lifecycle.BrickSupervisor, "start", _wont_start)
        with pytest.raises(lifecycle.BrickStartError):
            await k.rebind("echo", config={"greeting": "hi"})

        assert k.registry.resolve("echo") is original
        assert len([s for s in k._supervisors if s.contract == "echo"]) == 1
        assert await k.call("echo", "say", {"text": "still up"}) == {"text": "still up"}
    finally:
        await k.stop()
