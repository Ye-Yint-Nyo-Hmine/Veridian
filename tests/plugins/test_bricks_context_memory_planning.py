"""Phase 5: context, memory, and planning bricks — hermetic (no model required)."""

from __future__ import annotations

import textwrap

import pytest

from veridian.kernel import Kernel, load_stack


async def _kernel(tmp_path, body: str) -> Kernel:
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path)
    await k.start()
    return k


def _seed_workspace(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "auth.py").write_text(
        "def authenticate(user, password):\n    '''check credentials'''\n    return user == 'admin'\n",
        encoding="utf-8",
    )
    (tmp_path / "pkg" / "views.py").write_text(
        "from pkg.auth import authenticate\n\ndef login_view(req):\n    return authenticate(req.user, req.pw)\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Demo\nThis project handles authentication and login.\n", encoding="utf-8")


CONTEXT_BRICKS = ["default", "graph"]  # vector + semantic need a provider -> live tests


@pytest.mark.parametrize("which", CONTEXT_BRICKS)
async def test_context_index_and_retrieve(tmp_path, which):
    _seed_workspace(tmp_path)
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "c"
        [policy]
        grant = ["workspace:read"]
        [bindings]
        context = "bricks/context/{which}"
        """,
    )
    try:
        idx = await k.call("context", "index", {})
        assert idx["indexed"] > 0
        res = await k.call("context", "retrieve", {"query": "authenticate user", "k": 3})
        assert res["chunks"]
        assert any("auth" in c["path"] for c in res["chunks"])
    finally:
        await k.stop()


async def test_context_default_invalidate(tmp_path):
    _seed_workspace(tmp_path)
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "c"
        [policy]
        grant = ["workspace:read"]
        [bindings]
        context = "bricks/context/default"
        """,
    )
    try:
        await k.call("context", "index", {})
        inv = await k.call("context", "invalidate", {})
        assert inv["invalidated"] >= 1
    finally:
        await k.stop()


MEMORY_BRICKS = ["ephemeral", "vector", "graph"]


@pytest.mark.parametrize("which", MEMORY_BRICKS)
async def test_memory_write_read_search_forget(tmp_path, which):
    grant = '["workspace:write", "contract:model_provider"]' if which == "vector" else '["workspace:write"]'
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "m"
        [policy]
        grant = {grant}
        [bindings]
        memory = {{ brick = "bricks/memory/{which}", config = {{ in_memory = true }} }}
        """,
    )
    try:
        w = await k.call("memory", "write", {"content": "the deploy key rotates monthly", "tags": ["ops"]})
        rid = w["id"]
        rec = await k.call("memory", "read", {"id": rid})
        assert rec["record"]["content"].startswith("the deploy key")

        found = await k.call("memory", "search", {"query": "deploy key rotation", "k": 5})
        assert any(r["id"] == rid for r in found["results"])

        f = await k.call("memory", "forget", {"id": rid})
        assert f["forgotten"] == 1
        assert (await k.call("memory", "read", {"id": rid}))["record"] is None
    finally:
        await k.stop()


async def test_memory_graph_neighbours(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "m"
        [policy]
        grant = ["workspace:write"]
        [bindings]
        memory = { brick = "bricks/memory/graph", config = { in_memory = true } }
        """,
    )
    try:
        a = (await k.call("memory", "write", {"content": "root cause was a race in the scheduler"}))["id"]
        await k.call(
            "memory",
            "write",
            {"content": "unrelated note about billing", "metadata": {"links": [a]}},
        )
        res = await k.call("memory", "search", {"query": "scheduler race", "k": 5})
        ids = {r["id"] for r in res["results"]}
        assert a in ids
        assert len(ids) >= 2  # neighbour pulled in
    finally:
        await k.stop()


PLANNERS = ["default", "recursive", "tree-search"]


@pytest.mark.parametrize("which", PLANNERS)
async def test_planner_walks_to_completion(tmp_path, which):
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "p"
        [bindings]
        planner = "bricks/planning/{which}"
        """,
    )
    try:
        plan = await k.call("planner", "plan", {"goal": "write the file and run the tests and commit"})
        pid = plan["plan_id"]
        assert plan["steps"]
        seen = 0
        while True:
            nxt = await k.call("planner", "next", {"plan_id": pid, "observations": [{"status": "ok"}]})
            if nxt["step"] is None:
                break
            seen += 1
            assert seen < 50
        done = await k.call("planner", "is_complete", {"plan_id": pid})
        assert done["complete"] is True
        assert seen >= 3
    finally:
        await k.stop()


async def test_planner_default_stops_on_failure(tmp_path):
    k = await _kernel(
        tmp_path,
        """
        [stack]
        name = "p"
        [bindings]
        planner = "bricks/planning/default"
        """,
    )
    try:
        pid = (await k.call("planner", "plan", {"goal": "step one and step two and step three"}))["plan_id"]
        await k.call("planner", "next", {"plan_id": pid})
        await k.call("planner", "next", {"plan_id": pid, "observations": [{"status": "failed"}]})
        done = await k.call("planner", "is_complete", {"plan_id": pid})
        assert done["complete"] is True
        assert "fail" in done["reason"]
    finally:
        await k.stop()
