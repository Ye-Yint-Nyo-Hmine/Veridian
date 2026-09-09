"""Phase 5: sandbox/local and the tools bricks, driven by the real kernel."""

from __future__ import annotations

import shutil
import sys
import textwrap

import pytest

from veridian.kernel import Kernel, load_stack


def _stack(tmp_path, body: str):
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return load_stack(p)


async def _kernel(tmp_path, body: str) -> Kernel:
    k = Kernel(_stack(tmp_path, body), workspace_root=tmp_path)
    await k.start()
    return k


async def test_sandbox_exec_and_write(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "s"
        [policy]
        grant = ["process:spawn", "workspace:read", "workspace:write"]
        [bindings]
        sandbox = "bricks/sandbox/local"
        """,
    )
    try:
        w = await k.call("sandbox", "write", {"path": "hello.txt", "content": "hi there"})
        assert w["bytes_written"] == 8
        r = await k.call(
            "sandbox",
            "exec",
            {"command": [sys.executable, "-c", "import pathlib;print(pathlib.Path('hello.txt').read_text())"]},
        )
        assert r["exit_code"] == 0
        assert "hi there" in r["stdout"]
        assert r["timed_out"] is False
    finally:
        await k.stop()


async def test_sandbox_exec_timeout(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "s"
        [policy]
        grant = ["process:spawn"]
        [bindings]
        sandbox = "bricks/sandbox/local"
        """,
    )
    try:
        r = await k.call(
            "sandbox",
            "exec",
            {"command": [sys.executable, "-c", "import time;time.sleep(5)"], "timeout_ms": 400},
        )
        assert r["timed_out"] is True
    finally:
        await k.stop()


async def test_sandbox_path_escape_rejected(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "s"
        [policy]
        grant = ["process:spawn", "workspace:write"]
        [bindings]
        sandbox = "bricks/sandbox/local"
        """,
    )
    try:
        from veridian.contracts.errors import ProtocolError

        with pytest.raises(ProtocolError):
            await k.call("sandbox", "write", {"path": "../escape.txt", "content": "x"})
    finally:
        await k.stop()


async def test_filesystem_tools(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "s"
        [policy]
        grant = ["workspace:read", "workspace:write"]
        [bindings]
        tools = "bricks/tools/filesystem"
        """,
    )
    try:
        listing = await k.call("tools", "list", {})
        names = {t["name"] for t in listing["tools"]}
        assert {"read_file", "write_file", "list_dir", "search"} <= names

        await k.call("tools", "invoke", {"name": "write_file", "input": {"path": "a/b.txt", "content": "needle here"}})
        out = await k.call("tools", "invoke", {"name": "read_file", "input": {"path": "a/b.txt"}})
        assert out["output"] == "needle here"

        found = await k.call("tools", "invoke", {"name": "search", "input": {"pattern": "needle", "glob": "*.txt"}})
        assert "b.txt" in found["output"]
    finally:
        await k.stop()


async def test_terminal_tool_delegates_to_sandbox(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "s"
        [policy]
        grant = ["process:spawn", "contract:sandbox"]
        [bindings]
        sandbox = "bricks/sandbox/local"
        tools = "bricks/tools/terminal"
        """,
    )
    try:
        res = await k.call(
            "tools",
            "invoke",
            {"name": "run_command", "input": {"command": f'"{sys.executable}" -c "print(2+2)"'}},
        )
        assert "4" in res["output"]
        assert res["is_error"] is False
    finally:
        await k.stop()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_typescript_git_brick_alongside_python(tmp_path):
    # a scratch git repo
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("hello\n", encoding="utf-8")

    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "s"
        [policy]
        grant = ["process:spawn", "workspace:read", "workspace:write"]
        [bindings]
        tools = "bricks/tools/git"
        """,
    )
    try:
        st = await k.call("tools", "invoke", {"name": "git_status", "input": {}})
        assert "f.txt" in st["output"]
        await k.call("tools", "invoke", {"name": "git_add", "input": {"paths": ["f.txt"]}})
        cm = await k.call("tools", "invoke", {"name": "git_commit", "input": {"message": "add f"}})
        assert cm["is_error"] is False
        lg = await k.call("tools", "invoke", {"name": "git_log", "input": {"n": 1}})
        assert "add f" in lg["output"]
    finally:
        await k.stop()
