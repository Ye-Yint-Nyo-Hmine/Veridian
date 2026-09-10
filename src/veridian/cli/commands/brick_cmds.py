"""`veridian brick ...`"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from veridian.cli._common import bricks_root, console, err_console
from veridian.conformance import run_conformance_sync
from veridian.contracts.errors import ProtocolError
from veridian.plugin_runtime.environments import (
    EnvironmentError_,
    environment_status,
    resolve_environment,
)
from veridian.plugin_runtime.loader import discover_with_errors, resolve_brick
from veridian.plugin_runtime.manifest import load_manifest
from veridian.security.trust import assess

app = typer.Typer(no_args_is_help=True, help="Discover, inspect, and validate bricks.")


def _resolve(ref: str):
    return resolve_brick(ref, search_roots=[bricks_root()], repo_root=bricks_root().parent)


@app.command("list")
def list_() -> None:
    """List every brick under bricks/."""
    found, errors = discover_with_errors(bricks_root())
    table = Table("brick", "contracts", "runtime", "requires", "env")
    unresolved: list[tuple[str, str]] = []
    for name, m in sorted(found.items()):
        status = environment_status(m)
        if status in ("missing", "stale"):
            unresolved.append((name, status))
        table.add_row(
            name,
            ", ".join(sorted(m.implements)),
            m.runtime,
            ", ".join(m.requires) or "-",
            status,
        )
    console.print(table)
    for name, status in unresolved:
        err_console.print(
            f"[yellow]warning[/] {name}: environment is [bold]{status}[/] — refused at spawn "
            f"until `veridian brick install {name}`"
        )
    for path, exc in errors:
        err_console.print(f"[red]invalid[/] {path}: {exc}")


@app.command()
def inspect(ref: str) -> None:
    """Show a brick's manifest and trust assessment."""
    m = _resolve(ref)
    console.print(f"[bold]{m.name}[/] {m.version} — {m.description or ''}")
    console.print(f"runtime: {m.runtime}   protocol: {m.protocol}")
    console.print(f"spawn: {' '.join(m.spawn_command)}")
    for contract, methods in sorted(m.implements.items()):
        console.print(f"  implements [cyan]{contract}[/]: {', '.join(methods)}")
    console.print(f"requires: {m.requires or '-'}")
    console.print(f"env passthrough: {m.env_passthrough or '-'}")
    if m.needs_isolated_env():
        status = environment_status(m)
        console.print(f"dependencies: {m.dependencies}  [dim](environment: {status})[/]")
        if status == "ok":
            console.print(f"interpreter: {m.resolved_interpreter()}")
        else:
            err_console.print(
                f"[yellow]warning[/] environment is [bold]{status}[/] — this brick will be "
                f"refused at spawn; run `veridian brick install {m.name}`"
            )
    a = assess(m)
    console.print(f"trust: [bold]{a.level.name}[/] — {a.advisory}")


@app.command()
def install(
    ref: str = typer.Argument(None),
    all_: bool = typer.Option(False, "--all", help="Install every brick that declares dependencies."),
    force: bool = typer.Option(False, "--force", help="Rebuild even if the environment is current."),
    no_sdk: bool = typer.Option(False, "--no-sdk", help="Skip installing the veridian SDK into python venvs."),
) -> None:
    """Resolve a brick's private environment from its manifest's ``[dependencies]`` table.

    A python brick gets a venv under ``.veridian/venv`` resolved with ``uv``; a node brick gets a
    local ``node_modules`` via ``npm``. Bricks with no ``[dependencies]`` table are left alone.
    """
    if all_:
        found, _ = discover_with_errors(bricks_root())
        manifests = [m for m in found.values() if m.needs_isolated_env()]
        if not manifests:
            console.print("no bricks under bricks/ declare a [dependencies] table")
            return
    elif ref:
        manifests = [_resolve(ref)]
    else:
        err_console.print("give a brick ref or --all")
        raise typer.Exit(2)

    table = Table("brick", "runtime", "result", "interpreter / detail")
    failed = 0
    for m in manifests:
        try:
            res = resolve_environment(m, with_sdk=not no_sdk, force=force)
            table.add_row(res.brick, res.runtime, res.action, res.interpreter or res.detail or "-")
        except (EnvironmentError_, ProtocolError) as exc:
            failed += 1
            table.add_row(m.name, m.runtime, "[red]FAILED[/]", str(exc).splitlines()[0])
    console.print(table)
    raise typer.Exit(1 if failed else 0)


@app.command()
def validate(
    ref: str = typer.Argument(None), all_: bool = typer.Option(False, "--all", help="Validate every brick.")
) -> None:
    """Validate one brick's manifest, or every brick's with --all."""
    if all_:
        found, errors = discover_with_errors(bricks_root())
        for name in sorted(found):
            console.print(f"[green]OK[/] {name}")
        for path, exc in errors:
            err_console.print(f"[red]INVALID[/] {path}: {exc}")
        raise typer.Exit(1 if errors else 0)
    if not ref:
        err_console.print("give a brick ref or --all")
        raise typer.Exit(2)
    try:
        m = _resolve(ref)
    except (ProtocolError, FileNotFoundError) as exc:
        err_console.print(f"[red]INVALID[/] {ref}: {exc}")
        raise typer.Exit(1)
    console.print(f"[green]OK[/] {m.name}")


@app.command()
def conformance(
    ref: str = typer.Argument(None),
    all_: bool = typer.Option(False, "--all", help="Run against every brick."),
    timeout: float = typer.Option(30.0, help="Per-call timeout in seconds."),
) -> None:
    """Run the conformance harness: call every method of every contract a brick claims and
    validate both directions against the schemas."""
    if all_:
        found, _ = discover_with_errors(bricks_root())
        targets = [m.directory for m in found.values()]
    elif ref:
        targets = [_resolve(ref).directory]
    else:
        err_console.print("give a brick ref or --all")
        raise typer.Exit(2)

    failed = 0
    for brick_dir in targets:
        report = run_conformance_sync(brick_dir, timeout=timeout)
        colour = "green" if report.ok else "red"
        console.print(f"[{colour}]{report.summary()}[/]")
        for r in report.results:
            mark = "[green]ok[/]" if r.ok else "[red]FAIL[/]"
            note = f" — {r.detail}" if r.detail and (not r.ok or r.errored_cleanly) else ""
            console.print(f"    {mark} {r.contract}.{r.method}{note}")
        for p in report.problems:
            console.print(f"    [red]problem[/] {p}")
        failed += 0 if report.ok else 1
    raise typer.Exit(1 if failed else 0)
