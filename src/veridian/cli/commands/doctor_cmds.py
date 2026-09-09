"""`veridian doctor` — environment and configuration checks."""

from __future__ import annotations

import os
import shutil
import sys

import typer
from rich.table import Table

from veridian.cli._common import bricks_root, console, default_stack, repo_root
from veridian.contracts._schemas import check_all_schemas_valid
from veridian.kernel.config import load_stack
from veridian.plugin_runtime.loader import discover_with_errors


def doctor() -> None:
    """Check that Veridian can run here. Exits non-zero if a hard requirement fails."""
    table = Table("check", "status", "detail")
    hard_fail = False

    def row(name: str, ok: bool, detail: str = "", hard: bool = True) -> None:
        nonlocal hard_fail
        mark = "[green]ok[/]" if ok else ("[red]FAIL[/]" if hard else "[yellow]warn[/]")
        table.add_row(name, mark, detail)
        if not ok and hard:
            hard_fail = True

    row("python >= 3.13", sys.version_info >= (3, 13), sys.version.split()[0])

    problems = check_all_schemas_valid()
    row("JSON schemas valid", not problems, "; ".join(problems) or f"{len(problems)} problems")

    try:
        found, errors = discover_with_errors(bricks_root())
        row("bricks discoverable", not errors, f"{len(found)} bricks, {len(errors)} invalid")
    except Exception as exc:  # noqa: BLE001
        row("bricks discoverable", False, str(exc))

    ds = default_stack()
    if ds.is_file():
        try:
            s = load_stack(ds)
            row("stacks/default.toml", True, f"{len(s.active())} bindings")
        except Exception as exc:  # noqa: BLE001
            row("stacks/default.toml", False, str(exc))
    else:
        row("stacks/default.toml", False, "missing", hard=False)

    row("node (for TypeScript bricks)", shutil.which("node") is not None,
        shutil.which("node") or "not found", hard=False)
    row("git (for tools/git)", shutil.which("git") is not None,
        shutil.which("git") or "not found", hard=False)

    providers = [v for v in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if os.environ.get(v)]
    row("an inference provider", bool(providers),
        ", ".join(providers) or "none set (kernel still runs; `veridian run` needs one)", hard=False)

    console.print(table)
    console.print(f"repo root: {repo_root()}")
    if hard_fail:
        raise typer.Exit(1)
