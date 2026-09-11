"""`veridian stack ...`"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer
from rich.table import Table

from veridian.cli._common import console, err_console, repo_root, stack_roots
from veridian.kernel.config import load_stack, resolve_stack_ref
from veridian.kernel.errors import StackConfigError
from veridian.plugin_runtime.acquire import AcquireError, add_stack, stack_install_record

app = typer.Typer(no_args_is_help=True, help="Acquire, inspect, and validate stacks.")


def _resolve_path(ref: str) -> Path:
    return resolve_stack_ref(ref, repo_root=repo_root())


@dataclass(frozen=True)
class DiscoveredStack:
    """One stack found on the search path: its resolved file, the ``[stack].name`` it declares,
    the contracts it binds, where it came from, and — if it does not load — why."""

    path: Path
    name: str
    bindings: tuple[str, ...]
    source: str
    error: str | None = None


def discover_stacks() -> list[DiscoveredStack]:
    """Every stack reachable through the ordered stack roots (project, user, built-in,
    pre-installed), first occurrence of a path winning. Invalid files are included with their
    error rather than dropped, so a user can see why one will not load."""
    builtin_dir = (repo_root() / "stacks").resolve()
    out: list[DiscoveredStack] = []
    seen: set[Path] = set()
    for root in stack_roots():
        if not root.path.is_dir():
            continue
        for f in sorted(root.path.glob("*.toml")):
            rp = f.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            if root.path.resolve() == builtin_dir:
                source = "built-in"
            elif root.label == "pre-installed":
                source = "pre-installed"
            else:
                rec = stack_install_record(f.stem)
                source = rec.source if rec else root.label
            try:
                s = load_stack(f)
                out.append(DiscoveredStack(rp, s.name, tuple(s.bound_map()), source))
            except StackConfigError as exc:
                out.append(DiscoveredStack(rp, f.stem, (), source, str(exc)))
    return out


@app.command("list")
def list_() -> None:
    """List built-in stacks (stacks/) and any installed under VERIDIAN_HOME, with their source."""
    table = Table("stack", "bindings", "source", "path")
    for e in discover_stacks():
        if e.error is None:
            table.add_row(e.name, ", ".join(e.bindings), e.source, str(e.path))
        else:
            table.add_row(e.name, "[red]invalid[/]", e.source, e.error)
    console.print(table)


@app.command()
def add(
    source: str = typer.Argument(..., help="Local .toml path, git URL, or archive URL."),
    ref: str = typer.Option(None, "--ref", help="git branch/tag to clone (or use 'url#ref')."),
    path_in_repo: str = typer.Option(None, "--path", help="Path to the stack .toml inside the source."),
    force: bool = typer.Option(False, "--force", help="Overwrite an installed stack of the same name."),
) -> None:
    """Acquire a stack file from ``source`` and install it under ``VERIDIAN_HOME/stacks``.

    Afterwards ``--stack <name>`` (on ``veridian`` / ``veridian run``) resolves it by name."""
    try:
        result = add_stack(source, ref=ref, path_in_repo=path_in_repo, force=force)
    except AcquireError as exc:
        err_console.print(f"[red]add failed:[/] {exc}")
        raise typer.Exit(1)
    console.print(f"[green]installed[/] stack {result.name!r} -> {result.path}")
    console.print(f"  source: {result.record.source} ({result.record.source_kind})")
    if result.record.git_commit:
        console.print(f"  commit: {result.record.git_commit}")
    console.print(f"  hash:   {result.record.content_hash}")
    console.print(f"\nuse it:  veridian --stack {result.name}   (or: veridian run --stack {result.name} ...)")


@app.command()
def remove(name: str) -> None:
    """Remove an installed stack from ``VERIDIAN_HOME/stacks`` (built-in stacks are never touched)."""
    from veridian.plugin_runtime.home import home_stacks

    dest = home_stacks() / f"{name}.toml"
    if not dest.is_file():
        err_console.print(f"[red]remove failed:[/] no installed stack named {name!r} ({dest})")
        raise typer.Exit(1)
    dest.unlink()
    rec = home_stacks() / ".veridian" / f"{name}.install.json"
    if rec.is_file():
        rec.unlink()
    console.print(f"[green]removed[/] {dest}")


@app.command()
def show(stack: str) -> None:
    """Show the resolved bindings and policy for a stack (by name or path)."""
    s = load_stack(_resolve_path(stack))
    console.print(f"[bold]{s.name}[/] — {s.description or '(no description)'}")
    table = Table("contract", "brick", "disabled", "isolation", "egress", "config keys")
    for b in s.bindings:
        iso = b.manifest.isolation
        if not iso.is_container:
            isolation, egress = "process", "-"
        elif not iso.network:
            isolation, egress = f"container ({iso.image})", "denied"
        elif iso.allow_hosts:
            isolation, egress = f"container ({iso.image})", ", ".join(iso.allow_hosts)
        else:
            isolation, egress = f"container ({iso.image})", "[red]UNRESTRICTED[/]"
        table.add_row(
            b.contract,
            b.manifest.name,
            "yes" if b.disabled else "",
            isolation,
            egress,
            ", ".join(b.config) or "-",
        )
    console.print(table)
    console.print(f"policy grant: {sorted(s.policy.grant) or '-'}")
    console.print(f"policy deny:  {sorted(s.policy.deny) or '-'}")

    reachable = sorted(
        {
            host
            for b in s.active()
            for host in b.manifest.isolation.allow_hosts
            if b.manifest.isolation.is_container and b.manifest.isolation.network
        }
    )
    console.print(f"network destinations (allowlisted): {reachable or '-'}")
    unrestricted = [
        b.manifest.name for b in s.active() if b.manifest.isolation.unrestricted_egress
    ]
    if unrestricted:
        console.print(f"[red]unrestricted egress:[/] {', '.join(unrestricted)}")


@app.command()
def validate(stack: str) -> None:
    """Validate a stack (by name or path): schema, brick resolution, contract match."""
    try:
        path = _resolve_path(stack)
        s = load_stack(path)
    except StackConfigError as exc:
        err_console.print(f"[red]INVALID[/] {stack}\n{exc}")
        raise typer.Exit(1)
    console.print(f"[green]OK[/] {path} — {len(s.active())} active binding(s): {', '.join(s.bound_map())}")
