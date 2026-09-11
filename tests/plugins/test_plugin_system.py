"""The brick/stack plugin system: VERIDIAN_HOME, ordered search roots, acquisition, versioning,
and `[requires]` compatibility.

Hermetic: every "third-party" brick is a dependency-free SDK brick generated in a tmp dir, so
nothing here touches the network, `uv`, or a container engine.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from veridian.kernel import Kernel, load_stack, resolve_stack_ref
from veridian.kernel.errors import StackConfigError
from veridian.plugin_runtime.acquire import (
    AlreadyInstalled,
    ConsentDeclined,
    add_brick,
    add_stack,
    classify_source,
    content_hash,
    remove_brick,
)
from veridian.plugin_runtime.home import home_bricks, home_stacks, veridian_home
from veridian.plugin_runtime.loader import installed_versions, resolve_brick_ref
from veridian.plugin_runtime.search import brick_search_roots
from veridian.plugin_runtime.versions import InvalidVersionSpec, highest, satisfies, version_key

REPO = Path(__file__).resolve().parents[2]


def _write_tools_brick(
    directory: Path,
    *,
    name: str = "thirdparty/greeter",
    version: str = "1.0.0",
    requires: list[str] | None = None,
    requires_veridian: str | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    caps = f"\n[capabilities]\nrequires = {requires!r}\n" if requires else ""
    compat = f'\n[requires]\nveridian = "{requires_veridian}"\n' if requires_veridian else ""
    (directory / "veridian.toml").write_text(
        textwrap.dedent(
            f"""\
            name = "{name}"
            version = "{version}"
            description = "A third-party tools brick the project did not ship."
            protocol = "veridian/1.1"
            runtime = "python"

            [spawn]
            command = ["${{python}}", "brick.py"]

            [[implements]]
            contract = "tools"
            methods = ["list", "invoke"]
            """
        )
        + caps
        + compat,
        encoding="utf-8",
    )
    (directory / "brick.py").write_text(
        textwrap.dedent(
            f"""\
            from veridian.sdk import Brick, rpc, run

            class Greeter(Brick):
                name = "{name}"
                version = "{version}"
                implements = {{"tools": ["list", "invoke"]}}

                @rpc("tools.list")
                async def list_(self, params, ctx):
                    return {{"tools": [{{
                        "name": "greet",
                        "description": "say hello",
                        "input_schema": {{"type": "object", "properties": {{"who": {{"type": "string"}}}}}},
                    }}]}}

                @rpc("tools.invoke")
                async def invoke(self, params, ctx):
                    who = (params.get("input") or {{}}).get("who", "world")
                    return {{"output": f"hello {{who}}", "is_error": False}}

            if __name__ == "__main__":
                run(Greeter())
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


# -- 1. VERIDIAN_HOME -----------------------------------------------------------


def test_veridian_home_defaults_to_dot_veridian(monkeypatch):
    monkeypatch.delenv("VERIDIAN_HOME", raising=False)
    assert veridian_home() == (Path.home() / ".veridian").resolve()


def test_veridian_home_env_override(home):
    assert veridian_home() == home.resolve()
    assert home_bricks() == home.resolve() / "bricks"
    assert home_stacks() == home.resolve() / "stacks"


def test_home_is_not_created_at_import(home):
    # Just resolving the path must not touch the filesystem.
    assert not home.exists()


# -- 2. ordered search roots --------------------------------------------------


def test_search_root_precedence_order(home):
    proj = Path.cwd()
    roots = brick_search_roots(repo_root=REPO, project_dir=proj)
    assert [r.label for r in roots] == ["project", "user", "builtin"]
    assert roots[0].path == (proj / "bricks").resolve()
    assert roots[1].path == home.resolve() / "bricks"
    assert roots[2].path == (REPO / "bricks").resolve()


def test_first_match_wins_user_over_builtin(home, tmp_path):
    ext = _write_tools_brick(tmp_path / "ext" / "greeter", name="tools/filesystem", version="9.9.9")
    add_brick(str(ext), yes=True)  # shadows the built-in tools/filesystem by name
    # project_dir points at an empty dir so the precedence being tested is user-vs-builtin.
    roots = brick_search_roots(repo_root=REPO, project_dir=tmp_path / "empty-project")
    resolved = resolve_brick_ref(
        "tools/filesystem",
        search_roots=[(r.label, r.path) for r in roots],
        repo_root=REPO,
    )
    assert resolved.origin == "user"
    assert resolved.manifest.version == "9.9.9"

    # And with nothing installed, the same name falls through to the built-in copy.
    remove_brick("tools/filesystem")
    resolved2 = resolve_brick_ref(
        "tools/filesystem",
        search_roots=[(r.label, r.path) for r in roots],
        repo_root=REPO,
    )
    assert resolved2.origin == "builtin"


# -- 3. acquisition: brick add / remove --------------------------------------


def test_classify_source():
    assert classify_source("/tmp/foo") == "local"
    assert classify_source("./bricks/x") == "local"
    assert classify_source("https://example.com/x.tar.gz") == "archive"
    assert classify_source("https://example.com/x.zip") == "archive"
    assert classify_source("git+https://github.com/a/b") == "git"
    assert classify_source("https://github.com/a/b") == "git"
    assert classify_source("git@github.com:a/b.git") == "git"


def test_add_brick_from_local_path_copies_into_home(home, tmp_path):
    ext = _write_tools_brick(tmp_path / "ext" / "greeter")
    res = add_brick(str(ext), yes=True)
    assert res.path == home.resolve() / "bricks" / "thirdparty" / "greeter" / "1.0.0"
    assert (res.path / "veridian.toml").is_file()
    assert (res.path / "brick.py").is_file()
    # copied, not linked
    assert not res.path.is_symlink()
    assert not (res.path / "brick.py").is_symlink()


def test_add_brick_records_provenance(home, tmp_path):
    ext = _write_tools_brick(tmp_path / "ext" / "greeter")
    res = add_brick(str(ext), yes=True)
    rec = res.record
    assert rec.source == str(ext.resolve())
    assert rec.source_kind == "local"
    assert rec.content_hash.startswith("sha256:")
    assert rec.content_hash == content_hash(res.path)
    assert rec.installed_at.endswith("Z")
    assert (res.path / ".veridian" / "install.json").is_file()


def test_add_brick_refuses_duplicate_without_force(home, tmp_path):
    ext = _write_tools_brick(tmp_path / "ext" / "greeter")
    add_brick(str(ext), yes=True)
    with pytest.raises(AlreadyInstalled):
        add_brick(str(ext), yes=True)
    # --force replaces it
    res = add_brick(str(ext), yes=True, force=True)
    assert res.version == "1.0.0"


def test_remove_brick(home, tmp_path):
    _write_tools_brick(tmp_path / "e1" / "g", version="1.0.0")
    _write_tools_brick(tmp_path / "e2" / "g", version="1.1.0")
    add_brick(str(tmp_path / "e1" / "g"), yes=True)
    add_brick(str(tmp_path / "e2" / "g"), yes=True)
    assert set(installed_versions("thirdparty/greeter", [home_bricks()])) == {"1.0.0", "1.1.0"}

    remove_brick("thirdparty/greeter", "1.0.0")
    assert set(installed_versions("thirdparty/greeter", [home_bricks()])) == {"1.1.0"}

    remove_brick("thirdparty/greeter")
    assert installed_versions("thirdparty/greeter", [home_bricks()]) == {}
    # namespace dir pruned
    assert not (home_bricks() / "thirdparty").exists()


def test_consent_prompt_fires_only_with_dependencies(home, tmp_path):
    # no [dependencies] -> installs without ever calling confirm
    ext = _write_tools_brick(tmp_path / "nodep" / "g")

    def _boom(_msg):  # pragma: no cover - must not be called
        raise AssertionError("confirm called for a dependency-free brick")

    add_brick(str(ext), confirm=_boom)

    # with [dependencies] -> confirm is consulted, and a False answer aborts the install
    dep = tmp_path / "dep" / "g"
    _write_tools_brick(dep, name="thirdparty/depgreeter")
    (dep / "veridian.toml").write_text(
        (dep / "veridian.toml").read_text(encoding="utf-8")
        + '\n[dependencies]\npython = ["idna>=3"]\n',
        encoding="utf-8",
    )
    seen = []
    with pytest.raises(ConsentDeclined):
        add_brick(str(dep), confirm=lambda msg: seen.append(msg) or False, resolve_env=True)
    assert seen and "uv pip install" in seen[0]
    assert not (home_bricks() / "thirdparty" / "depgreeter").exists()


# -- 4. versioning -----------------------------------------------------------


def test_version_key_and_highest():
    assert version_key("1.2.0") == (1, 2, 0)
    assert version_key("v0.10.3+local") == (0, 10, 3)
    assert highest(["0.1.0", "0.10.0", "0.2.0"]) == "0.10.0"


def test_unpinned_resolves_to_highest_installed(home, tmp_path):
    for v in ("0.1.0", "0.2.0", "0.10.0"):
        d = tmp_path / f"e{v}" / "g"
        _write_tools_brick(d, version=v)
        add_brick(str(d), yes=True)
    roots = [(r.label, r.path) for r in brick_search_roots(repo_root=REPO)]
    assert resolve_brick_ref("thirdparty/greeter", search_roots=roots, repo_root=REPO).manifest.version == "0.10.0"
    # explicit pin wins
    assert resolve_brick_ref("thirdparty/greeter@0.2.0", search_roots=roots, repo_root=REPO).manifest.version == "0.2.0"
    with pytest.raises(FileNotFoundError):
        resolve_brick_ref("thirdparty/greeter@9.9.9", search_roots=roots, repo_root=REPO)


def test_requires_veridian_specifier():
    assert satisfies("0.1.0", ">=0.1,<0.2")
    assert not satisfies("0.2.0", ">=0.1,<0.2")
    assert satisfies("0.1.0", "")
    with pytest.raises(InvalidVersionSpec):
        satisfies("0.1.0", "=> 0.1")


def test_incompatible_requires_veridian_fails_stack_load(home, tmp_path):
    ext = _write_tools_brick(tmp_path / "ext" / "g", requires_veridian=">=99.0")
    add_brick(str(ext), yes=True)
    stack = tmp_path / "s.toml"
    stack.write_text(
        '[stack]\nname = "s"\n[policy]\ngrant = ["contract:tools"]\n'
        '[bindings]\ntools = "thirdparty/greeter"\n',
        encoding="utf-8",
    )
    with pytest.raises(StackConfigError) as ei:
        load_stack(stack)
    msg = str(ei.value)
    assert ">=99.0" in msg and "0.1.0" in msg  # both versions named


# -- 5. stacks are installable --------------------------------------------------


def test_stack_add_and_resolve_by_name(home, tmp_path):
    src = tmp_path / "my.toml"
    src.write_text(
        '[stack]\nname = "teamstack"\n[policy]\ngrant = ["contract:tools"]\n'
        '[bindings]\ntools = "bricks/tools/filesystem"\n',
        encoding="utf-8",
    )
    res = add_stack(str(src))
    assert res.path == home_stacks() / "teamstack.toml"
    assert res.record.source == str(src.resolve())
    # resolve_stack_ref finds it by name
    assert resolve_stack_ref("teamstack", repo_root=REPO) == res.path.resolve()


# -- 7. end to end: install a brick from a local path, bind by name, run it -----


async def test_end_to_end_install_bind_by_name_and_run(home, tmp_path):
    ext = _write_tools_brick(tmp_path / "outside-the-repo" / "greeter")
    add_brick(str(ext), yes=True)

    stack = tmp_path / "third_party.toml"
    stack.write_text(
        textwrap.dedent(
            """\
            [stack]
            name = "third-party"
            description = "A stack running a brick the project did not ship."

            [policy]
            grant = ["contract:tools", "workspace:read"]

            [bindings]
            tools = "thirdparty/greeter"
            """
        ),
        encoding="utf-8",
    )

    resolved = load_stack(stack)
    assert resolved.binding_for("tools").manifest.name == "thirdparty/greeter"

    kernel = Kernel(resolved, workspace_root=tmp_path)
    await kernel.start()
    try:
        listed = await kernel.call("tools", "list", {})
        assert [t["name"] for t in listed["tools"]] == ["greet"]
        out = await kernel.call("tools", "invoke", {"name": "greet", "input": {"who": "veridian"}})
        assert out["output"] == "hello veridian"
    finally:
        await kernel.stop()
