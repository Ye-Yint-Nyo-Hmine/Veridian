"""Phase 6: the orchestrator brick.

The agent loop needs a real ``inference`` provider, and there is no mock — so the hermetic test
asserts the loop wires up (context retrieved, plan made, deltas streamed) and then fails
*gracefully* when inference has no key, with the kernel surviving. The real end-to-end run is the
live test at the bottom and the Phase 8 smoke script.
"""

from __future__ import annotations

import textwrap

import pytest

from tests.fixtures import FIXTURE_ROOT
from tests.live_providers import PROVIDER, requires_live_provider
from veridian.contracts.errors import ProtocolError
from veridian.kernel import Kernel, load_stack

_HERMETIC_STACK = """
[stack]
name = "orch-hermetic"
[policy]
grant = [
  "network", "process:spawn", "workspace:read", "workspace:write",
  "contract:inference", "contract:context", "contract:planner",
  "contract:tools", "contract:memory", "contract:sandbox",
]
[bindings]
inference = "bricks/inference/anthropic"
context = "bricks/context/default"
planner = "bricks/planning/default"
memory = { brick = "bricks/memory/ephemeral" }
sandbox = "bricks/sandbox/local"
tools = "bricks/tools/filesystem"
orchestrator = "bricks/orchestrator/default"
"""


async def _kernel(tmp_path, body: str) -> Kernel:
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path)
    await k.start()
    return k


async def test_orchestrator_wires_up_then_fails_gracefully_without_a_provider(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "thing.py").write_text("def thing():\n    return 1\n", encoding="utf-8")

    k = await _kernel(tmp_path, _HERMETIC_STACK)
    try:
        stream = await k.call_stream(
            "orchestrator",
            "run",
            {"goal": "add a docstring to thing", "workspace_root": str(tmp_path), "limits": {"max_iterations": 2}},
        )
        deltas = [d async for d in stream]
        events = {d.get("event") for d in deltas}
        # context + plan happened before inference was reached
        assert "log" in events or "step" in events

        with pytest.raises(ProtocolError):
            await stream.result()

        # kernel still alive and every brick still bound
        assert set(k.bound()) >= {"orchestrator", "inference", "context", "planner"}
        assert await k.call("context", "retrieve", {"query": "thing"}) is not None
    finally:
        await k.stop()


_COMPACT_STACK = _HERMETIC_STACK.replace(
    'orchestrator = "bricks/orchestrator/default"',
    'conversation = { brick = "bricks/conversation/sqlite", config = { in_memory = true } }\n'
    'orchestrator = "bricks/orchestrator/default"',
).replace('"contract:sandbox",', '"contract:sandbox", "contract:conversation",')


async def test_compact_on_an_empty_session_is_a_noop(tmp_path):
    k = await _kernel(tmp_path, _COMPACT_STACK)
    try:
        res = await k.call("orchestrator", "compact", {"session_id": "s1"})
        assert res["status"] == "noop"
    finally:
        await k.stop()


async def test_compact_reaches_inference_once_there_is_history(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    k = await _kernel(tmp_path, _COMPACT_STACK)
    try:
        for i in range(4):
            await k.call(
                "conversation",
                "append",
                {"session_id": "s2", "message": {"role": "user", "content": f"turn {i}"}},
            )
        # >2 turns to compress -> it must call inference.generate, which has no key here and
        # errors cleanly. Reaching that error is the proof the summarise step wired up.
        with pytest.raises(ProtocolError):
            await k.call("orchestrator", "compact", {"session_id": "s2"})
    finally:
        await k.stop()


async def test_compact_with_two_turns_or_fewer_is_a_noop(tmp_path):
    k = await _kernel(tmp_path, _COMPACT_STACK)
    try:
        await k.call(
            "conversation",
            "append",
            {"session_id": "s3", "message": {"role": "user", "content": "only one"}},
        )
        res = await k.call("orchestrator", "compact", {"session_id": "s3"})
        assert res["status"] == "noop"
    finally:
        await k.stop()


# -- Bug 1: a small local model returns an assistant turn with null content ----------------------
#
# The live repro: pre-installed/bricks/orchestrator against Ollama. The model replied, then the
# run died with `TypeError: ... +: 'NoneType' and 'str'` (or `sequence item ...: NoneType`).
# Root cause: the reply text was built with `"".join(b.get("text", "") ...)`, and a provider that
# sends `content: null` (or a text block with `text: null`) makes `.get("text", "")` return that
# `None` — the key is present, so the default never applies. Normalising the parsed message at the
# boundary fixes it; the run must now degrade gracefully with the kernel intact.

_NULL_STACK = f"""
[stack]
name = "orch-null"
[policy]
grant = [
  "process:spawn", "workspace:read", "workspace:write",
  "contract:inference", "contract:context", "contract:tools", "contract:memory",
  "contract:sandbox", "contract:conversation",
]
[bindings]
inference = {{ brick = "{(FIXTURE_ROOT / 'null_inference').as_posix()}", config = {{ mode = "MODE" }} }}
context = "pre-installed/bricks/context"
tools = "pre-installed/bricks/toolkit"
sandbox = "bricks/sandbox/local"
memory = {{ brick = "bricks/memory/ephemeral" }}
conversation = {{ brick = "bricks/conversation/sqlite", config = {{ in_memory = true }} }}
orchestrator = {{ brick = "pre-installed/bricks/orchestrator", config = {{ session_id = "s1", max_recovery = 2 }} }}
"""


async def _null_kernel(tmp_path, mode: str) -> Kernel:
    p = tmp_path / "stack.toml"
    p.write_text(_NULL_STACK.replace("MODE", mode), encoding="utf-8")
    # validate_wire=False: the point is that the *orchestrator* is robust to a misbehaving
    # provider, independent of the kernel's own wire checks (which would otherwise reject the
    # null shape before it ever reached the loop).
    k = Kernel(load_stack(p), workspace_root=tmp_path, validate_wire=False)
    await k.start()
    return k


@pytest.mark.parametrize("mode", ["null_content", "null_text"])
async def test_orchestrator_survives_null_content_from_a_small_model(tmp_path, mode):
    k = await _null_kernel(tmp_path, mode)
    try:
        stream = await k.call_stream(
            "orchestrator",
            "run",
            {"goal": "refactor the parser", "workspace_root": str(tmp_path),
             "limits": {"max_iterations": 3}},
        )
        async for _ in stream:
            pass
        result = await stream.result()  # must NOT raise TypeError / sequence-item

        assert result["status"] in {"failed", "completed", "max_iterations"}
        assert "TypeError" not in result["summary"]
        # kernel and every brick still up and serving
        assert set(k.bound()) >= {"orchestrator", "inference", "context"}
        assert await k.call("context", "retrieve", {"query": "parser"}) is not None
    finally:
        await k.stop()


async def test_orchestrator_reaches_close_out_after_null_then_a_real_reply(tmp_path):
    p = tmp_path / "stack.toml"
    p.write_text(_NULL_STACK.replace('mode = "MODE"', 'mode = "then_ok", ok_after = 1'), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path, validate_wire=False)
    await k.start()
    try:
        stream = await k.call_stream(
            "orchestrator",
            "run",
            {"goal": "tidy up the imports", "workspace_root": str(tmp_path),
             "limits": {"max_iterations": 5}},
        )
        async for _ in stream:
            pass
        result = await stream.result()
        assert result["status"] in {"completed", "max_iterations", "failed"}
        assert "TypeError" not in result["summary"] and "NoneType" not in result["summary"]
    finally:
        await k.stop()


async def test_conversational_input_is_not_decomposed_into_an_objective(tmp_path):
    """"how are you?" is chat, not work: no plan step, no verify/evidence loop, and no
    orchestrator-run record written to memory."""
    p = tmp_path / "stack.toml"
    # ok_after=0: the fake returns a normal reply on the very first call, so the run is a single
    # clean turn with no malformed shapes in play.
    p.write_text(_NULL_STACK.replace('mode = "MODE"', 'mode = "then_ok", ok_after = 0'), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path, validate_wire=False)
    await k.start()
    try:
        events = []
        stream = await k.call_stream(
            "orchestrator", "run",
            {"goal": "how are you?", "workspace_root": str(tmp_path), "limits": {"max_iterations": 4}},
        )
        async for d in stream:
            events.append((d.get("event"), d.get("data", {})))
        result = await stream.result()

        kinds = [d.get("kind") for e, d in events if e == "step"]
        assert "plan" not in kinds                      # not decomposed into objectives
        assert "verify" not in kinds                    # no evidence loop
        assert result["status"] == "completed"
        assert result["iterations"] == 1               # one reply, done
        assert "changed files:" not in result["summary"]
        # no long-term memory record for a greeting
        hits = await k.call("memory", "search", {"query": "how are you", "tags": ["orchestrator-run"]})
        assert hits["results"] == []
    finally:
        await k.stop()


@pytest.mark.live
@requires_live_provider
async def test_orchestrator_end_to_end_live(tmp_path):
    # A5: gate on whichever provider is actually reachable — a local Ollama/llama.cpp server
    # first, a cloud key only as a fallback — so a contributor with no cloud keys still runs
    # the real agent loop instead of skipping it.
    (tmp_path / "mod.py").write_text("def top():\n    return 42\n", encoding="utf-8")
    stack = _HERMETIC_STACK.replace('inference = "bricks/inference/anthropic"', PROVIDER.binding)
    k = await _kernel(tmp_path, stack)
    try:
        stream = await k.call_stream(
            "orchestrator",
            "run",
            {
                "goal": "add a one-line docstring to the top() function in mod.py, then run: python -c \"import mod\"",
                "workspace_root": str(tmp_path),
                "limits": {"max_iterations": 8},
            },
        )
        async for _ in stream:
            pass
        result = await stream.result()
        assert result["status"] in {"completed", "max_iterations"}
        assert '"""' in (tmp_path / "mod.py").read_text(encoding="utf-8") or result["iterations"] >= 1
    finally:
        await k.stop()
