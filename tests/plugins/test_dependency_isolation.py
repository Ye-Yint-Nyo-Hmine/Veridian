"""A1 verification: two bricks that pin incompatible versions of the same package both start and
pass conformance in one stack.

Marked ``install`` because it resolves real environments with ``uv`` over the network. Run it
with::

    uv run pytest -m install tests/plugins/test_dependency_isolation.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from veridian.conformance import run_conformance
from veridian.kernel import Kernel, load_stack
from veridian.plugin_runtime.environments import resolve_environment
from veridian.plugin_runtime.manifest import load_manifest

pytestmark = pytest.mark.install

REPO = Path(__file__).resolve().parents[2]
OLD = REPO / "tests" / "fixtures" / "deps" / "six_old"
NEW = REPO / "tests" / "fixtures" / "deps" / "six_new"


@pytest.fixture(scope="module")
def installed():
    old = resolve_environment(load_manifest(OLD), with_sdk=False, force=True)
    new = resolve_environment(load_manifest(NEW), with_sdk=False, force=True)
    return old, new


def _six_version(interpreter: str) -> str:
    out = subprocess.run(
        [interpreter, "-c", "import six; print(six.__version__)"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def test_each_brick_gets_its_own_interpreter_and_pin(installed):
    old, new = installed
    assert old.interpreter and new.interpreter
    assert old.interpreter != new.interpreter
    assert _six_version(old.interpreter) == "1.15.0"
    assert _six_version(new.interpreter) == "1.16.0"


async def test_both_conform_with_their_private_environments(installed):
    for brick_dir in (OLD, NEW):
        report = await run_conformance(brick_dir, timeout=60.0)
        assert report.ok, f"{report.summary()} problems={report.problems}"


async def test_both_run_in_one_stack(installed, tmp_path):
    k = Kernel(load_stack(REPO / "tests" / "fixtures" / "stacks" / "deps_isolation.toml"),
               workspace_root=tmp_path)
    await k.start()
    try:
        assert set(k.bound()) == {"tools", "workspace"}
        tools = await k.call("tools", "list", {})
        assert tools["tools"] == []
        entries = await k.call("workspace", "list", {})
        assert entries["entries"] == []
    finally:
        await k.stop()
