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
