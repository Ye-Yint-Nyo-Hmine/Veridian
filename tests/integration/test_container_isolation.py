"""Milestone 2 / A2 + A3 end-to-end: a brick with ``isolation.mode = "container"`` really starts
inside a container, ``network = false`` really denies it egress, and ``network = true`` with an
``allow_hosts`` allowlist lets it reach exactly the listed host and nothing else.

Marked ``container`` — deselected unless a Docker/Podman engine is on PATH. The argv and the
allowlist plumbing this depends on are covered hermetically by
``tests/plugins/test_container_spawn.py`` and ``tests/security/test_egress.py``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from veridian.kernel import Kernel, load_stack
from veridian.plugin_runtime.process import detect_container_engine

pytestmark = pytest.mark.container

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "echo" / "containerized"


def _needs_engine():
    if detect_container_engine() is None:
        pytest.skip("no container engine (docker/podman) on PATH")


def _sweep_egress_resources(engine: str) -> list[str]:
    """Force-remove any ``veridian-egress-*`` proxy container or network. Returns what it removed
    so a test can assert nothing leaked."""
    removed: list[str] = []
    for kind, ls in (("container", ["ps", "-aq", "--filter", "name=veridian-egress-proxy-"]),
                     ("network", ["network", "ls", "-q", "--filter", "name=veridian-egress-"])):
        try:
            out = subprocess.run([engine, *ls], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for ident in out.stdout.split():
            rm = ["rm", "-f", ident] if kind == "container" else ["network", "rm", ident]
            try:
                if subprocess.run([engine, *rm], capture_output=True, timeout=20).returncode == 0:
                    removed.append(f"{kind}:{ident}")
            except (OSError, subprocess.TimeoutExpired):
                pass
    return removed


@pytest.fixture(autouse=True)
def no_leaked_egress_resources():
    """Belt-and-suspenders around the code's own teardown: sweep before each test (clearing
    anything an earlier crashed run left behind) and fail the test if it leaked on the way out."""
    engine = detect_container_engine()
    if engine is None:
        yield
        return
    _sweep_egress_resources(engine)
    yield
    leaked = _sweep_egress_resources(engine)
    assert not leaked, f"container test leaked egress resources: {leaked}"


def _brick_dir(tmp_path: Path, isolation_toml: str) -> Path:
    d = tmp_path / "brick"
    d.mkdir()
    shutil.copy(_FIXTURE / "brick.py", d / "brick.py")
    (d / "veridian.toml").write_text(
        textwrap.dedent(
            f"""
            name = "toolbox/containerized"
            version = "0.1.0"
            protocol = "veridian/1.0"
            runtime = "python"
            [spawn]
            command = ["${{python}}", "brick.py"]
            [[implements]]
            contract = "tools"
            methods = ["list", "invoke"]
            {isolation_toml}
            """
        ),
        encoding="utf-8",
    )
    return d


async def _kernel(tmp_path: Path, brick_dir: Path) -> Kernel:
    (tmp_path / "stack.toml").write_text(
        textwrap.dedent(
            f"""
            [stack]
            name = "container-e2e"
            [policy]
            grant = ["contract:tools"]
            [bindings]
            tools = {{ brick = "{brick_dir.as_posix()}" }}
            """
        ),
        encoding="utf-8",
    )
    k = Kernel(load_stack(tmp_path / "stack.toml"), workspace_root=tmp_path)
    await k.start()
    return k


async def _probe(k: Kernel, host: str, port: int = 443) -> dict:
    res = await k.call("tools", "invoke", {"name": "probe", "input": {"host": host, "port": port}})
    return json.loads(res["output"])


# The probe fixture makes a bare ``urllib`` HTTPS GET and calls the host "reachable" only on a
# clean 2xx. ``one.one.one.one`` was a poor target for the allow case: the CONNECT tunnel opens
# fine but Cloudflare answers a header-less GET with a real ``403``, so the probe reported
# "unreachable" even though egress control was working -- it made a passing proxy look broken.
# ``example.com`` (IANA's reserved domain) returns ``200`` to a bare GET and is about as stable a
# target as exists; ``example.org`` is a different, unlisted host for the deny case (the proxy
# refuses its CONNECT before the origin is ever contacted, so its own response never matters).
_ALLOW_TARGET = "example.com"
_DENY_TARGET = "example.org"


async def test_network_false_denies_all_egress(tmp_path):
    _needs_engine()
    d = _brick_dir(
        tmp_path,
        '[isolation]\nmode = "container"\nimage = "python:3.13-slim"\nnetwork = false\n',
    )
    k = await _kernel(tmp_path, d)
    try:
        assert (await _probe(k, _ALLOW_TARGET))["reachable"] is False
    finally:
        await k.stop()


async def test_allowlist_permits_listed_host_only(tmp_path):
    _needs_engine()
    d = _brick_dir(
        tmp_path,
        '[isolation]\nmode = "container"\nimage = "python:3.13-slim"\n'
        f'network = true\nallow_hosts = ["{_ALLOW_TARGET}:443"]\n',
    )
    k = await _kernel(tmp_path, d)
    try:
        assert (await _probe(k, _ALLOW_TARGET))["reachable"] is True
        assert (await _probe(k, _DENY_TARGET))["reachable"] is False
    finally:
        await k.stop()
