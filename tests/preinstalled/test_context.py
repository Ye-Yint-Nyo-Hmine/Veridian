"""context/repo-map — repository map, selective retrieval, large-file outline, AGENT.md loading."""

from __future__ import annotations

import textwrap

from veridian.kernel import Kernel, load_stack

_STACK = """
[stack]
name = "context-test"
[policy]
grant = ["contract:context", "workspace:read"]
[bindings]
context = { brick = "pre-installed/bricks/context", config = { CONFIG } }
"""


async def _kernel(tmp_path, ws, **config) -> Kernel:
    p = tmp_path / "stack.toml"
    body = ", ".join(f"{k} = {v!r}" for k, v in config.items())
    p.write_text(textwrap.dedent(_STACK).replace("CONFIG", body), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=ws)
    await k.start()
    return k


async def test_retrieve_returns_a_repository_map_and_ranked_chunks(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "auth.py").write_text("def login(user):\n    return check_password(user)\n", encoding="utf-8")
    (ws / "misc.py").write_text("def unrelated():\n    return 0\n", encoding="utf-8")
    k = await _kernel(tmp_path, ws)
    try:
        res = await k.call("context", "retrieve", {"query": "login password check", "k": 4})
        paths = [c["path"] for c in res["chunks"]]
        assert "<repository map>" in paths
        rmap = next(c for c in res["chunks"] if c["path"] == "<repository map>")
        assert "auth.py" in rmap["text"] and "lines)" in rmap["text"]
        # the relevant file outranks the irrelevant one among the code chunks
        code = [c["path"] for c in res["chunks"] if c["path"] not in {"<repository map>"}]
        assert code and code[0] == "auth.py"
    finally:
        await k.stop()


async def test_large_file_comes_back_as_a_symbol_outline(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    big = ["import os"] + [f"def func_{i}(x):\n    return x + {i}\n" for i in range(300)]
    (ws / "big.py").write_text("\n".join(big), encoding="utf-8")
    k = await _kernel(tmp_path, ws)
    try:
        res = await k.call("context", "retrieve", {"query": "func_42", "k": 3})
        chunk = next(c for c in res["chunks"] if c["path"] == "big.py")
        assert "outline only" in chunk["text"]
        assert "def func_0" in chunk["text"] and "symbol outline" in chunk["text"]
        # not the raw 900-line body
        assert chunk["text"].count("return x +") < 200
    finally:
        await k.stop()


async def test_agent_md_user_and_workspace_both_load_workspace_authoritative(tmp_path, home):
    (home / "AGENT.md").write_text("User rule: prefer tabs.\n", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENT.md").write_text("Workspace rule: 4 spaces, never tabs.\n", encoding="utf-8")
    (ws / "code.py").write_text("x = 1\n", encoding="utf-8")
    k = await _kernel(tmp_path, ws)
    try:
        res = await k.call("context", "retrieve", {"query": "anything", "k": 3})
        by_path = {c["path"]: c for c in res["chunks"]}
        assert "AGENT.md (user guideline)" in by_path
        assert "AGENT.md (workspace guideline)" in by_path
        u = by_path["AGENT.md (user guideline)"]
        w = by_path["AGENT.md (workspace guideline)"]
        assert "prefer tabs" in u["text"] and "4 spaces" in w["text"]
        # workspace outranks user, and its text says it is authoritative
        assert w["score"] > u["score"]
        assert "authoritative" in w["text"].lower()
    finally:
        await k.stop()


async def test_agent_md_absent_is_fine(tmp_path, home):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "code.py").write_text("x = 1\n", encoding="utf-8")
    k = await _kernel(tmp_path, ws)
    try:
        res = await k.call("context", "retrieve", {"query": "code", "k": 3})
        paths = [c["path"] for c in res["chunks"]]
        assert not any("AGENT.md" in p for p in paths)
        assert "<repository map>" in paths
    finally:
        await k.stop()


async def test_indexing_stops_at_the_file_budget_and_says_so(tmp_path):
    """A workspace bigger than the budget yields a smaller index, not a failed call — and the
    truncation is stated in the repository map rather than left for the model to misread as a
    complete listing."""
    ws = tmp_path / "ws"
    ws.mkdir()
    for i in range(40):
        (ws / f"mod_{i:03d}.py").write_text(f"def widget_{i}():\n    return widget\n", encoding="utf-8")

    k = await _kernel(tmp_path, ws, max_files=5)
    try:
        res = await k.call("context", "retrieve", {"query": "widget", "k": 50})
        rmap = next(c for c in res["chunks"] if c["path"] == "<repository map>")
        assert "partial index" in rmap["text"]
        assert "5 files" in rmap["text"]
        # the budget bound the index, so far fewer chunks came back than there are files
        code = [c for c in res["chunks"] if c["path"].startswith("mod_")]
        assert 0 < len(code) <= 5
    finally:
        await k.stop()


async def test_a_workspace_inside_the_budget_is_not_reported_as_partial(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "only.py").write_text("def thing():\n    return 1\n", encoding="utf-8")
    k = await _kernel(tmp_path, ws)
    try:
        res = await k.call("context", "retrieve", {"query": "thing", "k": 5})
        rmap = next(c for c in res["chunks"] if c["path"] == "<repository map>")
        assert "partial index" not in rmap["text"]
    finally:
        await k.stop()


async def test_a_file_over_the_per_file_budget_is_skipped(tmp_path):
    """A bundle or a dump costs more to chunk than the recall it adds, and is the one entry that
    can stall a walk on a sync-backed filesystem."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "small.py").write_text("def thing():\n    return 1\n", encoding="utf-8")
    (ws / "dump.json").write_text('{"thing": "' + "x" * 5000 + '"}', encoding="utf-8")
    k = await _kernel(tmp_path, ws, max_file_bytes=1000)
    try:
        res = await k.call("context", "retrieve", {"query": "thing", "k": 10})
        paths = [c["path"] for c in res["chunks"]]
        assert "small.py" in paths
        assert "dump.json" not in paths
    finally:
        await k.stop()
