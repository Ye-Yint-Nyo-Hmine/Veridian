"""Shared fixtures for the pre-installed autonomous-agent tree."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PRE = REPO / "pre-installed"
BRICKS = PRE / "bricks"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated VERIDIAN_HOME so skills / AGENT.md tests never touch the real one."""
    h = tmp_path / "veridian-home"
    (h / "skills").mkdir(parents=True)
    monkeypatch.setenv("VERIDIAN_HOME", str(h))
    return h


def write_stack(path: Path, *, inference: str | None = None, extra_grant: str = "") -> Path:
    """The pre-installed autonomous stack, optionally with an inference binding line added."""
    body = (PRE / "stacks" / "autonomous.toml").read_text(encoding="utf-8")
    if inference:
        body = body.replace("[bindings]\n", f"[bindings]\n{inference}\n")
    path.write_text(body, encoding="utf-8")
    return path


def make_repo(ws: Path, files: dict[str, str]) -> None:
    import subprocess

    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = ws / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    run = lambda *a: subprocess.run(a, cwd=ws, check=True, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "initial")
