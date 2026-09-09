"""Phase 7 / Milestone 1 criteria 1-3: swap a subsystem without touching the kernel.

One scenario per subsystem, parameterized over stacks that differ only in which brick is bound.
The assertion is that the *identical* kernel code path runs green for every binding, and that the
kernel source tree is not modified to make any binding work.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from veridian.kernel import Kernel, load_stack

REPO = Path(__file__).resolve().parents[2]


async def _kernel(tmp_path: Path, body: str) -> Kernel:
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path)
    await k.start()
    return k


def _seed(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "auth.py").write_text(
        "def authenticate(user):\n    '''verify the user'''\n    return bool(user)\n", encoding="utf-8"
    )
    (tmp_path / "pkg" / "app.py").write_text(
        "from pkg.auth import authenticate\n\ndef handler(u):\n    return authenticate(u)\n", encoding="utf-8"
    )


# --- criterion 2: swap context ------------------------------------------------------

CONTEXT_BRICKS = ["default", "graph", "vector", "semantic"]


@pytest.mark.parametrize("which", CONTEXT_BRICKS)
async def test_swap_context(tmp_path, which):
    _seed(tmp_path)
    grant = '"workspace:read", "workspace:write", "contract:model_provider", "contract:inference"'
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "swap-context-{which}"
        [policy]
        grant = [{grant}]
        [bindings]
        context = "bricks/context/{which}"
        """,
    )
    try:
        # identical calls for every binding
        idx = await k.call("context", "index", {})
        assert idx["indexed"] >= 1
        res = await k.call("context", "retrieve", {"query": "authenticate user", "k": 3})
        assert isinstance(res["chunks"], list)
        assert any("auth" in c["path"] for c in res["chunks"])
    finally:
        await k.stop()


# --- criterion 1: swap inference ---------------------------------------------------

INFERENCE_BINDINGS = {
    "anthropic": 'inference = "bricks/inference/anthropic"',
    "openai": 'inference = "bricks/inference/openai"',
    "routing": (
        'inference = { brick = "bricks/inference/routing", '
        'config = { default = "p", providers = [ { name = "p", kind = "openai", model = "x" } ] } }'
    ),
}


@pytest.mark.parametrize("which", list(INFERENCE_BINDINGS))
async def test_swap_inference(tmp_path, monkeypatch, which):
    for v in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "swap-inference-{which}"
        [policy]
        grant = ["network"]
        [bindings]
        {INFERENCE_BINDINGS[which]}
        """,
    )
    try:
        # the identical call path for every provider binding
        models = await k.call("inference", "models", {})
        assert isinstance(models["models"], list) and models["models"]
    finally:
        await k.stop()


# --- criterion 3: swap sandbox ---------------------------------------------------

SANDBOX_BINDINGS = {
    "local": '"bricks/sandbox/local"',
    "custom": '"tests/fixtures/custom_sandbox"',
}


@pytest.mark.parametrize("which", list(SANDBOX_BINDINGS))
async def test_swap_sandbox(tmp_path, which):
    k = await _kernel(
        tmp_path,
        f"""
        [stack]
        name = "swap-sandbox-{which}"
        [policy]
        grant = ["process:spawn", "workspace:write"]
        [bindings]
        sandbox = {SANDBOX_BINDINGS[which]}
        """,
    )
    try:
        w = await k.call("sandbox", "write", {"path": "s.txt", "content": "hello"})
        assert w["bytes_written"] == 5
        r = await k.call("sandbox", "exec", {"command": [sys.executable, "-c", "print(6*7)"], "timeout_ms": 5000})
        assert r["exit_code"] == 0 and "42" in r["stdout"]
    finally:
        await k.stop()


# --- the literal check: the kernel tree is untouched ------------------------------


def test_kernel_source_tree_is_unmodified():
    """`git diff --stat -- src/veridian/kernel` must be empty: no binding above required editing
    the kernel."""
    out = subprocess.run(
        ["git", "diff", "--stat", "--", "src/veridian/kernel"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "", f"kernel tree has uncommitted changes:\n{out}"
