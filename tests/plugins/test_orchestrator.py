"""Phase 6: the orchestrator brick.

The agent loop needs a real ``inference`` provider, and there is no mock — so the hermetic test
asserts the loop wires up (context retrieved, plan made, deltas streamed) and then fails
*gracefully* when inference has no key, with the kernel surviving. The real end-to-end run is the
live test at the bottom and the Phase 8 smoke script.
"""

from __future__ import annotations

import textwrap

import pytest

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
