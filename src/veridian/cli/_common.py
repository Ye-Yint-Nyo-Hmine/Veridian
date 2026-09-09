"""Small shared helpers for the CLI."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from veridian.kernel.config import find_repo_root

console = Console()
err_console = Console(stderr=True)


def repo_root() -> Path:
    return find_repo_root(Path.cwd())


def bricks_root() -> Path:
    return repo_root() / "bricks"


def stacks_root() -> Path:
    return repo_root() / "stacks"


def default_stack() -> Path:
    return stacks_root() / "default.toml"
