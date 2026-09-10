"""Milestone 2 / A2: ``isolation.mode = "container"`` is a brick-spawn mode the kernel enforces
for any brick, not only for code the sandbox brick runs.

Hermetic. Nothing here needs Docker: the tests assert how the ``docker run`` / ``podman run`` argv
is *built*. The end-to-end "a container actually starts and cannot reach the network" check is
``tests/integration/test_container_isolation.py`` and is marked ``container`` (skipped when no
engine is installed).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.contracts.errors import INVALID_MANIFEST, ProtocolError
from veridian.plugin_runtime.manifest import IsolationSpec, load_manifest, parse_manifest
from veridian.plugin_runtime.process import (
    ContainerSpawn,
    ContainerUnavailable,
    ProcessSpawn,
    detect_container_engine,
    spawn_strategy_for,
)

_BASE = {
    "name": "demo/brick",
    "version": "0.1.0",
    "protocol": "veridian/1.0",
    "runtime": "python",
    "spawn": {"command": ["${python}", "brick.py"]},
    "implements": [{"contract": "tools", "methods": ["list", "invoke"]}],
}


def _manifest(tmp_path: Path, **isolation) -> "object":
    data = {**_BASE}
    if isolation:
        data["isolation"] = isolation
    (tmp_path / "brick.py").write_text("# fixture entrypoint\n", encoding="utf-8")
    return parse_manifest(data, tmp_path)


# -- manifest parsing --------------------------------------------------------------------------------


def test_no_isolation_table_is_process_mode(tmp_path):
    m = _manifest(tmp_path)
    assert m.isolation == IsolationSpec()
    assert m.isolation.is_container is False


def test_container_isolation_parses(tmp_path):
    m = _manifest(
        tmp_path,
        mode="container",
        image="python:3.13-slim",
        network=True,
        allow_hosts=["api.anthropic.com:443"],
    )
    assert m.isolation.is_container
    assert m.isolation.image == "python:3.13-slim"
    assert m.isolation.network is True
    assert m.isolation.allow_hosts == ("api.anthropic.com:443",)
    assert m.isolation.unrestricted_egress is False


def test_container_without_image_is_rejected(tmp_path):
    with pytest.raises(ProtocolError) as ei:
        _manifest(tmp_path, mode="container")
    assert ei.value.code == INVALID_MANIFEST
    assert "isolation.image" in str(ei.value)


def test_unrestricted_egress_flag(tmp_path):
    m = _manifest(tmp_path, mode="container", image="x", network=True)
    assert m.isolation.unrestricted_egress is True


# -- strategy selection -----------------------------------------------------------------------------


def test_spawn_strategy_for_defaults_to_process(tmp_path):
    assert isinstance(spawn_strategy_for(_manifest(tmp_path)), ProcessSpawn)


def test_spawn_strategy_for_container_needs_an_engine(tmp_path, monkeypatch):
    monkeypatch.setattr("veridian.plugin_runtime.process.shutil.which", lambda _n: None)
    m = _manifest(tmp_path, mode="container", image="python:3.13-slim")
    with pytest.raises(ContainerUnavailable):
        spawn_strategy_for(m)


def test_detect_container_engine_prefers_named(monkeypatch):
    monkeypatch.setattr(
        "veridian.plugin_runtime.process.shutil.which",
        lambda n: f"/usr/bin/{n}" if n in ("docker", "podman") else None,
    )
    assert detect_container_engine() == "docker"
    assert detect_container_engine("podman") == "podman"
    monkeypatch.setattr("veridian.plugin_runtime.process.shutil.which", lambda _n: None)
    assert detect_container_engine() is None


# -- argv construction ----------------------------------------------------------------------------


def _resolve(tmp_path, workspace, **isolation):
    m = _manifest(tmp_path, mode="container", image="python:3.13-slim", **isolation)
    strat = ContainerSpawn(m.isolation, engine="docker")
    env = {"SYSTEMROOT": r"C:\Windows", "PYTHONUNBUFFERED": "1", "ANTHROPIC_API_KEY": "sk-test"}
    return m, strat.resolve(m, workspace_root=workspace, brick_env=env)


def test_container_argv_mounts_workspace_and_brick(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    m, spawn = _resolve(tmp_path, ws)
    argv = spawn.argv
    assert argv[0] == "docker"
    assert argv[1:4] == ["run", "--rm", "-i"]
    assert "--network" in argv and argv[argv.index("--network") + 1] == "none"
    assert f"{ws.as_posix()}:/workspace" in argv
    assert f"{tmp_path.as_posix()}:/brick:ro" in argv
    assert argv[argv.index("-w") + 1] == "/workspace"
    # image then the rewritten brick command
    assert argv[-3:] == ["python:3.13-slim", "python", "/brick/brick.py"]
    assert spawn.cwd == str(ws)


def test_container_argv_scrubs_host_only_env_but_forwards_the_rest(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _m, spawn = _resolve(tmp_path, ws)
    joined = " ".join(spawn.argv)
    assert "SYSTEMROOT" not in joined
    assert "-e ANTHROPIC_API_KEY=sk-test" in joined
    assert "-e PYTHONUNBUFFERED=1" in joined


def test_container_argv_allowlisted_network_joins_the_internal_net_and_proxy(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    _m, spawn = _resolve(tmp_path, ws, network=True, allow_hosts=["api.anthropic.com:443"])
    net = spawn.argv[spawn.argv.index("--network") + 1]
    assert net.startswith("veridian-egress-int-")
    assert "none" not in spawn.argv
    assert any("HTTPS_PROXY=http://veridian-egress-proxy-" in a for a in spawn.argv)
    assert spawn.pre_run and spawn.post_run  # proxy + networks stood up / torn down


def test_process_spawn_is_unchanged(tmp_path):
    m = _manifest(tmp_path)
    spawn = ProcessSpawn().resolve(m, workspace_root=tmp_path, brick_env={"PATH": "x"})
    assert spawn.argv == m.resolved_command()
    assert spawn.env == {"PATH": "x"}
    assert spawn.cwd == str(tmp_path)
