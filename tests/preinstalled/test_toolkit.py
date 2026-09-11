"""tools/toolkit — filesystem, git, terminal-through-sandbox, and skills, all in one tools brick."""

from __future__ import annotations

import textwrap

import pytest

from tests.preinstalled.conftest import make_repo
from veridian.kernel import Kernel, load_stack

_STACK = """
[stack]
name = "toolkit-test"
[policy]
grant = ["contract:tools", "contract:sandbox", "workspace:read", "workspace:write", "process:spawn"]
[bindings]
tools = "pre-installed/bricks/toolkit"
sandbox = "bricks/sandbox/local"
"""


async def _kernel(tmp_path) -> Kernel:
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(_STACK), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path)
    await k.start()
    return k


async def _invoke(k, _tool, **inp):
    return await k.call("tools", "invoke", {"name": _tool, "input": inp})


async def test_list_exposes_fs_git_terminal_and_skill_mgmt_tools(tmp_path):
    k = await _kernel(tmp_path)
    try:
        names = {t["name"] for t in (await k.call("tools", "list", {}))["tools"]}
        assert {"read_file", "write_file", "edit_file", "list_dir", "search_files"} <= names
        assert "run_command" in names
        assert {"git_status", "git_diff", "git_log", "git_add", "git_commit"} <= names
        assert {"skill_list", "skill_create", "skill_modify"} <= names
    finally:
        await k.stop()


async def test_write_read_slice_and_edit(tmp_path):
    k = await _kernel(tmp_path)
    try:
        await _invoke(k, "write_file", path="a/b.py", content="one\ntwo\nthree\nfour\n")
        full = await _invoke(k, "read_file", path="a/b.py")
        assert "1\tone" in full["output"] and "4\tfour" in full["output"]

        sliced = await _invoke(k, "read_file", path="a/b.py", start_line=2, end_line=3)
        assert "two" in sliced["output"] and "one" not in sliced["output"].split("\n", 1)[1]

        ok = await _invoke(k, "edit_file", path="a/b.py", old_text="two", new_text="2")
        assert not ok.get("is_error")
        assert "2\n" in (await _invoke(k, "read_file", path="a/b.py"))["output"]
    finally:
        await k.stop()


async def test_edit_file_refuses_ambiguous_or_missing_match(tmp_path):
    k = await _kernel(tmp_path)
    try:
        await _invoke(k, "write_file", path="x.txt", content="dup\ndup\n")
        ambiguous = await _invoke(k, "edit_file", path="x.txt", old_text="dup", new_text="q")
        assert ambiguous["is_error"] and "unique" in ambiguous["output"]
        assert (await _invoke(k, "read_file", path="x.txt"))["output"].count("dup") == 2  # untouched

        missing = await _invoke(k, "edit_file", path="x.txt", old_text="nope", new_text="q")
        assert missing["is_error"] and "not found" in missing["output"]
    finally:
        await k.stop()


async def test_path_escape_is_a_hard_error(tmp_path):
    k = await _kernel(tmp_path)
    try:
        with pytest.raises(Exception):
            await _invoke(k, "read_file", path="../../etc/passwd")
    finally:
        await k.stop()


async def test_search_files(tmp_path):
    k = await _kernel(tmp_path)
    try:
        await _invoke(k, "write_file", path="s/one.py", content="def target():\n    pass\n")
        await _invoke(k, "write_file", path="s/two.md", content="target elsewhere\n")
        hits = await _invoke(k, "search_files", pattern=r"target", glob="*.py")
        assert "s/one.py:1:" in hits["output"] and "two.md" not in hits["output"]
    finally:
        await k.stop()


async def test_run_command_goes_through_the_sandbox_contract(tmp_path):
    k = await _kernel(tmp_path)
    try:
        res = await _invoke(k, "run_command", command="python -c \"print(6*7)\"")
        assert "42" in res["output"] and not res.get("is_error")
        bad = await _invoke(k, "run_command", command="python -c \"import sys; sys.exit(3)\"")
        assert bad["is_error"] and "exit=3" in bad["output"]
    finally:
        await k.stop()


async def test_git_tools_report_real_state(tmp_path):
    ws = tmp_path / "repo"
    make_repo(ws, {"f.py": "x = 1\n"})
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(_STACK), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=ws)
    await k.start()
    try:
        (ws / "f.py").write_text("x = 2\n", encoding="utf-8")
        status = await _invoke(k, "git_status")
        assert "f.py" in status["output"]
        diff = await _invoke(k, "git_diff")
        assert "-x = 1" in diff["output"] and "+x = 2" in diff["output"]
        log = await _invoke(k, "git_log", n=1)
        assert "initial" in log["output"]
    finally:
        await k.stop()


async def test_skills_are_loaded_and_exposed_as_tools(tmp_path, home):
    (home / "skills" / "tidy-imports").mkdir(parents=True)
    (home / "skills" / "tidy-imports" / "SKILL.md").write_text(
        textwrap.dedent(
            """\
            ---
            name: tidy-imports
            description: Sort and dedupe imports in a Python module.
            ---

            # Tidy imports

            1. Read the file.
            2. Group stdlib / third-party / local.
            """
        ),
        encoding="utf-8",
    )
    k = await _kernel(tmp_path)
    try:
        tools = {t["name"]: t for t in (await k.call("tools", "list", {}))["tools"]}
        assert "skill.tidy-imports" in tools
        assert "Sort and dedupe imports" in tools["skill.tidy-imports"]["description"]

        body = await _invoke(k, "skill.tidy-imports")
        assert "Group stdlib" in body["output"] and "# skill: tidy-imports" in body["output"]

        listing = await _invoke(k, "skill_list")
        assert "tidy-imports:" in listing["output"]
    finally:
        await k.stop()


async def test_skill_create_and_modify(tmp_path, home):
    k = await _kernel(tmp_path)
    try:
        created = await _invoke(
            k, "skill_create", name="Deploy Check", description="Pre-deploy checklist.",
            body="1. run tests\n2. bump version",
        )
        assert not created.get("is_error")
        md = (home / "skills" / "deploy-check" / "SKILL.md").read_text(encoding="utf-8")
        assert "name: deploy-check" in md and "Pre-deploy checklist." in md

        # visible on the next list, invokable
        tools = {t["name"] for t in (await k.call("tools", "list", {}))["tools"]}
        assert "skill.deploy-check" in tools

        await _invoke(k, "skill_modify", name="deploy-check", body="1. run tests\n2. tag release")
        md2 = (home / "skills" / "deploy-check" / "SKILL.md").read_text(encoding="utf-8")
        assert "tag release" in md2 and "Pre-deploy checklist." in md2  # description kept
    finally:
        await k.stop()
