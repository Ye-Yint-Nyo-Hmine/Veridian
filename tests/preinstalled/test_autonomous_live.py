"""End-to-end: drive the pre-installed autonomous stack against a real (preferably local) provider,
and prove the --resume round trip — a second kernel with the same session_id sees the history.

Marked ``live``; deselected by default. Runs when a local model server (or a cloud key) is
reachable — see tests/live_providers.py.
"""

from __future__ import annotations

import dataclasses
import subprocess
import sys
import textwrap

import pytest

from tests.live_providers import PROVIDER, requires_live_provider
from tests.preinstalled.conftest import PRE, make_repo
from veridian.kernel import Kernel, load_stack

pytestmark = [pytest.mark.live, requires_live_provider]


def _resolved_with_inference(stack_file, session_id: str):
    base = (PRE / "stacks" / "autonomous.toml").read_text(encoding="utf-8")
    base = base.replace("[bindings]\n", f"[bindings]\n{PROVIDER.binding}\n")
    stack_file.write_text(base, encoding="utf-8")
    resolved = load_stack(stack_file)
    for i, b in enumerate(resolved.bindings):
        if b.contract == "orchestrator":
            resolved.bindings[i] = dataclasses.replace(
                b, config={**b.config, "session_id": session_id}
            )
    return resolved


async def test_autonomous_run_edits_runs_recovers_and_reports(tmp_path):
    ws = tmp_path / "scratch"
    make_repo(
        ws,
        {
            "calc.py": "def add(a, b):\n    return a - b  # bug: should add\n",
            "test_calc.py": "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n",
        },
    )
    session_id = "live-e2e-1"
    resolved = _resolved_with_inference(tmp_path / "s.toml", session_id)
    k = Kernel(resolved, workspace_root=ws)
    await k.start()
    events = []
    try:
        stream = await k.call_stream(
            "orchestrator",
            "run",
            {
                "goal": "Fix add() in calc.py so it adds, then run `python -m pytest -q` and "
                "confirm the tests pass.",
                "workspace_root": str(ws),
                "limits": {"max_iterations": 14},
            },
        )
        async for d in stream:
            events.append(d)
        result = await stream.result()
    finally:
        await k.stop()

    assert result["status"] in {"completed", "max_iterations"}
    src = (ws / "calc.py").read_text(encoding="utf-8")
    assert "a + b" in src and "a - b" not in src
    # the tests really pass on disk now
    assert subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ws).returncode == 0
    # a verify step ran with real evidence, and the summary carries git evidence
    assert any(e.get("event") == "step" and e["data"].get("kind") == "verify" for e in events)
    assert "calc.py" in result["summary"]


async def test_resume_round_trip_brings_back_conversation_history(tmp_path):
    ws = tmp_path / "scratch"
    make_repo(ws, {"note.txt": "hello\n"})
    session_id = "live-resume-1"

    r1 = _resolved_with_inference(tmp_path / "s1.toml", session_id)
    k1 = Kernel(r1, workspace_root=ws)
    await k1.start()
    try:
        stream = await k1.call_stream(
            "orchestrator", "run",
            {"goal": "Append a line saying 'second' to note.txt.",
             "workspace_root": str(ws), "limits": {"max_iterations": 8}},
        )
        async for _ in stream:
            pass
        await stream.result()
    finally:
        await k1.stop()

    # a brand-new kernel, same session_id in the orchestrator binding config == what --resume does
    r2 = _resolved_with_inference(tmp_path / "s2.toml", session_id)
    k2 = Kernel(r2, workspace_root=ws)
    await k2.start()
    try:
        hist = await k2.call("conversation", "load", {"session_id": session_id, "limit": 50})
        roles = [m["message"]["role"] for m in hist["messages"]]
        assert roles.count("user") >= 1 and roles.count("assistant") >= 1
        assert any("second" in str(m["message"]["content"]) for m in hist["messages"])
    finally:
        await k2.stop()
