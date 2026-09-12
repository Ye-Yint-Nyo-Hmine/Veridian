"""Phase 3: stack config loading and the event bus."""

from __future__ import annotations

import asyncio

import pytest

from veridian.kernel import load_stack
from veridian.kernel.config import DEFAULT_CALL_TIMEOUT
from veridian.kernel.errors import StackConfigError
from veridian.kernel.events import Event, EventBus
from tests.kernel.conftest import STACKS


def test_load_stack_resolves_bindings():
    stack = load_stack(STACKS / "echo_roundtrip.toml")
    assert stack.name == "echo-roundtrip"
    assert stack.binding_for("echo").manifest.name == "echo/roundtrip"
    assert "contract:echo" in stack.policy.grant


def test_binding_call_timeout_defaults_and_overrides(tmp_path):
    """How long the kernel waits on a routed call is a property of the deployment, not a kernel
    constant: a hosted model and a 4B model on a laptop do not share a sensible ceiling."""
    body = '[stack]\nname="x"\n[bindings]\necho = "tests/fixtures/echo/ok"\n'
    p = tmp_path / "default.toml"
    p.write_text(body, encoding="utf-8")
    assert load_stack(p).binding_for("echo").call_timeout == DEFAULT_CALL_TIMEOUT

    p = tmp_path / "override.toml"
    p.write_text(
        '[stack]\nname="x"\n[bindings.echo]\nbrick = "tests/fixtures/echo/ok"\ncall_timeout = 600\n',
        encoding="utf-8",
    )
    assert load_stack(p).binding_for("echo").call_timeout == 600.0


def test_load_stack_rejects_a_non_positive_call_timeout(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text(
        '[stack]\nname="x"\n[bindings.echo]\nbrick = "tests/fixtures/echo/ok"\ncall_timeout = 0\n',
        encoding="utf-8",
    )
    with pytest.raises(StackConfigError):
        load_stack(p)


def test_load_stack_rejects_unknown_brick(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[stack]\nname="x"\n[bindings]\necho = "nowhere/nope"\n', encoding="utf-8")
    with pytest.raises(StackConfigError):
        load_stack(p)


def test_load_stack_rejects_contract_mismatch(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text(
        '[stack]\nname="x"\n[bindings]\nmemory = "tests/fixtures/echo/ok"\n', encoding="utf-8"
    )
    with pytest.raises(StackConfigError):
        load_stack(p)


def test_load_stack_rejects_invalid_schema(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text('[stack]\nname="x"\n', encoding="utf-8")  # no bindings
    with pytest.raises(StackConfigError):
        load_stack(p)


async def test_event_bus_glob_and_history():
    bus = EventBus()
    seen: list[str] = []
    bus.subscribe("brick.*", lambda e: seen.append(e.type))
    bus.emit(Event(type="brick.ready"))
    bus.emit(Event(type="kernel.ready"))
    bus.emit(Event(type="brick.stopped"))
    assert seen == ["brick.ready", "brick.stopped"]
    assert [e.type for e in bus.history("brick.*")] == ["brick.ready", "brick.stopped"]


async def test_event_bus_async_subscriber_and_bad_subscriber():
    bus = EventBus()
    got = asyncio.Event()

    async def ok(e):
        got.set()

    def boom(e):
        raise RuntimeError("nope")

    bus.subscribe("*", boom)
    bus.subscribe("*", ok)
    bus.emit(Event(type="x"))
    await asyncio.wait_for(got.wait(), 1.0)
    await bus.drain()
