"""`veridian stack ...`"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from veridian.cli._common import console, err_console, stacks_root
from veridian.kernel.config import load_stack
from veridian.kernel.errors import StackConfigError

app = typer.Typer(no_args_is_help=True, help="Inspect and validate stack files.")


@app.command("list")
def list_() -> None:
    """List the stack files in stacks/."""
    root = stacks_root()
    files = sorted(root.glob("*.toml")) if root.is_dir() else []
    if not files:
        console.print(f"[yellow]no stack files under {root}[/]")
        raise typer.Exit(0)
    table = Table("stack", "bindings", "path")
    for f in files:
        try:
            s = load_stack(f)
            table.add_row(s.name, ", ".join(s.bound_map()), str(f.relative_to(root.parent)))
        except StackConfigError as exc:
            table.add_row(f.stem, f"[red]invalid[/]", str(exc))
    console.print(table)


@app.command()
def show(path: Path) -> None:
    """Show the resolved bindings and policy for a stack."""
    s = load_stack(path)
    console.print(f"[bold]{s.name}[/] — {s.description or '(no description)'}")
    table = Table("contract", "brick", "disabled", "config keys")
    for b in s.bindings:
        table.add_row(
            b.contract,
            b.manifest.name,
            "yes" if b.disabled else "",
            ", ".join(b.config) or "-",
        )
    console.print(table)
    console.print(f"policy grant: {sorted(s.policy.grant) or '-'}")
    console.print(f"policy deny:  {sorted(s.policy.deny) or '-'}")


@app.command()
def validate(path: Path) -> None:
    """Validate a stack file: schema, brick resolution, contract match."""
    try:
        s = load_stack(path)
    except StackConfigError as exc:
        err_console.print(f"[red]INVALID[/] {path}\n{exc}")
        raise typer.Exit(1)
    console.print(f"[green]OK[/] {path} — {len(s.active())} active binding(s): {', '.join(s.bound_map())}")
