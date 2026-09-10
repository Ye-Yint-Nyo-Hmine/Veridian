"""Milestone 2 / A1 follow-up: a brick that declares a ``[dependencies]`` table but has no
private environment matching it is refused before it runs, never silently launched against the
kernel's own interpreter.

Hermetic: no environment is ever installed, so nothing here touches the network or ``uv``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.contracts.errors import INVALID_MANIFEST, ProtocolError
from veridian.kernel import Kernel, load_stack
from veridian.kernel.events import BRICK_READY, BRICK_STARTING
from veridian.plugin_runtime.manifest import UnresolvedEnvironment, load_manifest
from veridian.plugin_runtime.registry import check_environment_resolved

REPO = Path(__file__).resolve().parents[2]
UNINSTALLED = REPO / "tests" / "fixtures" / "deps" / "uninstalled"


def _stack(tmp_path: Path) -> Path:
    s = tmp_path / "s.toml"
    s.write_text(
        '[stack]\nname = "u"\n[bindings]\n' f'tools = "{UNINSTALLED.as_posix()}"\n',
        encoding="utf-8",
    )
    return s


async def test_kernel_refuses_to_start_a_brick_with_an_unresolved_environment(tmp_path):
    k = Kernel(load_stack(_stack(tmp_path)), workspace_root=tmp_path)
    with pytest.raises(UnresolvedEnvironment) as ei:
        await k.start()

    msg = str(ei.value)
    assert "deps/uninstalled" in msg
    assert "missing" in msg
    assert "veridian brick install" in msg  # actionable

    history = k.events.history()
    assert any(e.type == BRICK_STARTING for e in history)
    assert not any(e.type == BRICK_READY for e in history)  # never came up

    await k.stop()


def test_manifest_resolution_raises_rather_than_borrowing_the_kernel_interpreter():
    m = load_manifest(UNINSTALLED)
    assert m.environment_state() == "missing"
    with pytest.raises(UnresolvedEnvironment):
        m.resolved_command()


def test_registry_bind_check_rejects_the_same_brick_with_invalid_manifest():
    # The binding table carries the same refusal with the protocol error code, for any path that
    # reaches bind() with a resolved command (and as the seam A3 will extend for egress).
    with pytest.raises(ProtocolError) as ei:
        check_environment_resolved(load_manifest(UNINSTALLED))
    assert ei.value.code == INVALID_MANIFEST
