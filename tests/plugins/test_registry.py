"""Phase 2: the binding table refuses a brick that misrepresents itself."""

from __future__ import annotations

import pytest

from veridian.contracts.errors import INVALID_MANIFEST, ProtocolError
from veridian.plugin_runtime.manifest import load_manifest
from veridian.plugin_runtime.process import BrickProcess, base_env
from veridian.plugin_runtime.registry import (
    BrickHandle,
    PluginRegistry,
    cross_check_capabilities,
    parse_capabilities_result,
)
from tests.fixtures import ECHO_ROOT


def test_cross_check_passes_when_capabilities_cover_manifest():
    m = load_manifest(ECHO_ROOT / "ok")
    cross_check_capabilities(m, {"echo": ["say", "stream", "extra"]})


def test_cross_check_fails_on_missing_method():
    m = load_manifest(ECHO_ROOT / "capability_liar")  # declares say, stream, secret
    with pytest.raises(ProtocolError) as ei:
        cross_check_capabilities(m, {"echo": ["say", "stream"]})
    assert ei.value.code == INVALID_MANIFEST


def test_cross_check_fails_on_missing_contract():
    m = load_manifest(ECHO_ROOT / "ok")
    with pytest.raises(ProtocolError):
        cross_check_capabilities(m, {"something_else": ["x"]})


async def test_registry_refuses_to_bind_a_liar(tmp_path):
    m = load_manifest(ECHO_ROOT / "capability_liar")
    p = BrickProcess(m.name, m.resolved_command(), cwd=tmp_path, env=base_env([]))
    ep = await p.start()
    try:
        await ep.call(
            "plugin.initialize",
            {"protocol_version": "veridian/1.0", "capabilities": [], "workspace_root": ".", "config": {}},
            timeout=5.0,
        )
        caps = await ep.call("plugin.capabilities", {}, timeout=5.0)
        handle = BrickHandle(m, p, ep, reported_contracts=parse_capabilities_result(caps))
        reg = PluginRegistry()
        with pytest.raises(ProtocolError) as ei:
            reg.bind("echo", handle)
        assert ei.value.code == INVALID_MANIFEST
        assert reg.resolve("echo") is None
    finally:
        await p.stop()


async def test_registry_binds_a_truthful_brick(tmp_path):
    m = load_manifest(ECHO_ROOT / "ok")
    p = BrickProcess(m.name, m.resolved_command(), cwd=tmp_path, env=base_env([]))
    ep = await p.start()
    try:
        await ep.call(
            "plugin.initialize",
            {"protocol_version": "veridian/1.0", "capabilities": [], "workspace_root": ".", "config": {}},
            timeout=5.0,
        )
        caps = await ep.call("plugin.capabilities", {}, timeout=5.0)
        handle = BrickHandle(m, p, ep, reported_contracts=parse_capabilities_result(caps))
        reg = PluginRegistry()
        reg.bind("echo", handle)
        assert reg.resolve("echo") is handle
        assert reg.bound_contracts() == {"echo": "echo/ok"}
    finally:
        await p.stop()
