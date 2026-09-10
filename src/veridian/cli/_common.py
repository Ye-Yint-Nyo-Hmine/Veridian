"""Small shared helpers for the CLI."""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from veridian.cli.ui.theme import THEME
from veridian.kernel.config import find_repo_root

# Best effort: on Windows a piped stdout defaults to the ANSI codepage (cp1252) with strict
# error handling, so a stray non-ASCII byte in tool output would crash the write. UTF-8 with
# replacement keeps output flowing; a real console is already UTF-8 and unaffected.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass

console = Console(theme=THEME)
err_console = Console(stderr=True, theme=THEME)


def repo_root() -> Path:
    return find_repo_root(Path.cwd())


def bricks_root() -> Path:
    return repo_root() / "bricks"


def stacks_root() -> Path:
    return repo_root() / "stacks"


def default_stack() -> Path:
    return stacks_root() / "default.toml"
