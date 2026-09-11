"""Constraint: installing a brick grants it nothing. Capabilities come only from the stack policy
and the [isolation] table — never from the act of installation.

An installed third-party brick that *asks* for `network` in its manifest, bound in a stack whose
policy grants it nothing, gets no `network` capability. The kernel therefore denies every
network-gated host operation, and a container-mode spawn of the same brick would be `--network
none` (see veridian.security.egress). This is the honest half of "cannot reach the network" that
process mode can enforce today.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from veridian.kernel import Kernel, load_stack
from veridian.plugin_runtime.acquire import add_brick
from veridian.plugin_runtime.manifest import load_manifest
from veridian.security.egress import plan_egress
from veridian.security.policy import Policy

REPO = Path(__file__).resolve().parents[2]


def _write_network_hungry_tools_brick(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "veridian.toml").write_text(
        textwrap.dedent(
            """\
            name = "thirdparty/exfil"
            version = "0.1.0"
            description = "A third-party brick that asks for the network. It must not get it."
            protocol = "veridian/1.1"
            runtime = "python"

            [spawn]
            command = ["${python}", "brick.py"]

            [capabilities]
            requires = ["network"]

            [[implements]]
            contract = "tools"
            methods = ["list", "invoke"]
            """
        ),
        encoding="utf-8",
    )
    (directory / "brick.py").write_text(
        textwrap.dedent(
            """\
            from veridian.sdk import Brick, rpc, run

            class Exfil(Brick):
                name = "thirdparty/exfil"
                version = "0.1.0"
                implements = {"tools": ["list", "invoke"]}

                @rpc("tools.list")
                async def list_(self, params, ctx):
                    return {"tools": []}

                @rpc("tools.invoke")
                async def invoke(self, params, ctx):
                    return {"output": "", "is_error": False}

            if __name__ == "__main__":
                run(Exfil())
            """
        ),
        encoding="utf-8",
    )
    return directory


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "veridian-home"
    monkeypatch.setenv("VERIDIAN_HOME", str(h))
    return h


async def test_installed_third_party_brick_with_no_grant_has_no_network(home, tmp_path):
    ext = _write_network_hungry_tools_brick(tmp_path / "outside" / "exfil")
    add_brick(str(ext), yes=True)

    stack_file = tmp_path / "s.toml"
    stack_file.write_text(
        textwrap.dedent(
            """\
            [stack]
            name = "no-grant"

            [policy]
            grant = ["contract:tools"]

            [bindings]
            tools = "thirdparty/exfil"
            """
        ),
        encoding="utf-8",
    )
    resolved = load_stack(stack_file)

    # Policy layer: the brick requests network, policy grants none -> effective set has no network.
    assert "network" not in resolved.policy.effective_capabilities("thirdparty/exfil", ["network"])

    kernel = Kernel(resolved, workspace_root=tmp_path)
    await kernel.start()
    try:
        assert "network" not in kernel.effective_capabilities("thirdparty/exfil")
    finally:
        await kernel.stop()


def test_same_brick_in_container_mode_would_be_network_none(home, tmp_path):
    ext = _write_network_hungry_tools_brick(tmp_path / "outside" / "exfil")
    add_brick(str(ext), yes=True)
    m = load_manifest(tmp_path / "outside" / "exfil")
    # isolation defaults to process/network=false; a container spawn of it is --network none.
    plan = plan_egress(m.isolation, engine="docker", image="python:3.13-slim")
    assert plan.network_args == ("--network", "none")


def test_install_does_not_widen_policy(home, tmp_path):
    # An empty policy stays empty no matter what the installed manifest asks for.
    ext = _write_network_hungry_tools_brick(tmp_path / "outside" / "exfil")
    add_brick(str(ext), yes=True)
    empty = Policy()
    assert empty.effective_capabilities("thirdparty/exfil", ["network", "workspace:read"]) == set()
