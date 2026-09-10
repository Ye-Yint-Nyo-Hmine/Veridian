"""Milestone 2 / B2: a first-class, local conversation contract.

The orchestrator used to hold messages in loop state and drop them when a run ended, so chat
history did not survive a session. The `conversation` contract plus the SQLite reference brick
make history a durable, *local* thing — distinct from `memory`, and never sent anywhere.
"""

from __future__ import annotations

import textwrap

import pytest

from veridian.contracts.errors import ProtocolError
from veridian.kernel import Kernel, load_stack
from veridian.plugin_runtime.manifest import load_manifest

REPO_BRICK = "bricks/conversation/sqlite"


def _stack(tmp_path, body: str):
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return load_stack(p)


async def _kernel(tmp_path, body: str) -> Kernel:
    k = Kernel(_stack(tmp_path, body), workspace_root=tmp_path)
    await k.start()
    return k


_CONV_STACK = f"""
[stack]
name = "conv"
[policy]
grant = ["workspace:read", "workspace:write", "contract:conversation"]
[bindings]
conversation = "{REPO_BRICK}"
"""


async def test_append_load_list_delete_roundtrip(tmp_path):
    k = await _kernel(tmp_path, _CONV_STACK)
    try:
        a = await k.call("conversation", "append", {"session_id": "s1", "message": {"role": "user", "content": "hello"}})
        assert a["seq"] == 1 and a["id"]
        await k.call("conversation", "append", {"session_id": "s1", "message": {"role": "assistant", "content": "hi there"}})
        await k.call("conversation", "append", {"session_id": "s1", "message": {"role": "user", "content": "bye"}})

        loaded = await k.call("conversation", "load", {"session_id": "s1"})
        assert loaded["session_id"] == "s1"
        assert [e["message"]["content"] for e in loaded["messages"]] == ["hello", "hi there", "bye"]
        assert [e["seq"] for e in loaded["messages"]] == [1, 2, 3]

        tail = await k.call("conversation", "load", {"session_id": "s1", "limit": 1})
        assert [e["message"]["content"] for e in tail["messages"]] == ["bye"]

        sessions = await k.call("conversation", "list_sessions", {})
        assert len(sessions["sessions"]) == 1
        info = sessions["sessions"][0]
        assert info["session_id"] == "s1" and info["message_count"] == 3
        assert info["preview"] == "hello"

        gone = await k.call("conversation", "delete", {"session_id": "s1"})
        assert gone["deleted"] == 3
        assert (await k.call("conversation", "load", {"session_id": "s1"}))["messages"] == []
        assert (await k.call("conversation", "delete", {"session_id": "missing"}))["deleted"] == 0
    finally:
        await k.stop()


async def test_history_survives_across_two_separate_kernels(tmp_path):
    # first "veridian run"
    k1 = await _kernel(tmp_path, _CONV_STACK)
    try:
        await k1.call("conversation", "append", {"session_id": "abc", "message": {"role": "user", "content": "turn one"}})
    finally:
        await k1.stop()

    db = tmp_path / ".veridian" / "conversation.sqlite"
    assert db.is_file(), "conversation must persist to a local SQLite file under the workspace"

    # second, independent "veridian run" against the same workspace
    k2 = await _kernel(tmp_path, _CONV_STACK)
    try:
        loaded = await k2.call("conversation", "load", {"session_id": "abc"})
        assert [e["message"]["content"] for e in loaded["messages"]] == ["turn one"]
    finally:
        await k2.stop()


def test_reference_brick_is_local_only():
    m = load_manifest(__import__("pathlib").Path(REPO_BRICK).resolve())
    assert "network" not in m.requires, "the conversation brick must not be able to open a socket"
    assert m.requires == ["workspace:write"]


_ORCH_STACK = """
[stack]
name = "orch-conv"
[policy]
grant = [
  "network", "process:spawn", "workspace:read", "workspace:write",
  "contract:inference", "contract:context", "contract:planner",
  "contract:tools", "contract:memory", "contract:sandbox", "contract:conversation",
]
[bindings]
inference = "bricks/inference/anthropic"
context = "bricks/context/default"
planner = "bricks/planning/default"
memory = "bricks/memory/ephemeral"
sandbox = "bricks/sandbox/local"
tools = "bricks/tools/filesystem"
conversation = { brick = "bricks/conversation/sqlite" }
orchestrator = { brick = "bricks/orchestrator/default", config = { session_id = "chat-1" } }
"""


async def test_orchestrator_persists_turns_even_when_the_run_fails(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    k = await _kernel(tmp_path, _ORCH_STACK)
    try:
        # run 1 — no provider key, so the loop fails at inference. The user turn is still recorded.
        stream = await k.call_stream(
            "orchestrator", "run",
            {"goal": "first goal", "workspace_root": str(tmp_path), "limits": {"max_iterations": 1}},
        )
        async for _ in stream:
            pass
        with pytest.raises(ProtocolError):
            await stream.result()

        after_one = await k.call("conversation", "load", {"session_id": "chat-1"})
        assert [e["message"]["content"] for e in after_one["messages"]] == ["first goal"]

        # run 2 — the orchestrator loads the prior turn before it starts
        stream = await k.call_stream(
            "orchestrator", "run",
            {"goal": "second goal", "workspace_root": str(tmp_path), "limits": {"max_iterations": 1}},
        )
        deltas = [d async for d in stream]
        with pytest.raises(ProtocolError):
            await stream.result()
        assert any("loaded 1 prior turn" in str(d.get("data", {})) for d in deltas)

        contents = [e["message"]["content"] for e in (await k.call("conversation", "load", {"session_id": "chat-1"}))["messages"]]
        assert contents == ["first goal", "second goal"]
    finally:
        await k.stop()
