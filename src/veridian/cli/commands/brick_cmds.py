"""`veridian brick ...`"""

from __future__ import annotations

import typer
from rich.table import Table

from veridian.cli._common import brick_roots, bricks_root, console, err_console, repo_root
from veridian.conformance import run_conformance_sync
from veridian.contracts.errors import ProtocolError
from veridian.plugin_runtime.acquire import (
    AcquireError,
    InstallRecord,
    add_brick,
    classify_source,
    list_installed_bricks,
    remove_brick,
)
from veridian.plugin_runtime.environments import (
    EnvironmentError_,
    environment_status,
    resolve_environment,
)
from veridian.plugin_runtime.loader import discover_with_errors, installed_versions, resolve_brick_ref
from veridian.security.trust import assess

app = typer.Typer(no_args_is_help=True, help="Discover, acquire, inspect, and validate bricks.")


def _labelled_roots():
    return [(r.label, r.path) for r in brick_roots()]


def _resolve(ref: str):
    return resolve_brick_ref(ref, search_roots=_labelled_roots(), repo_root=repo_root()).manifest


@app.command("list")
def list_() -> None:
    """List every brick under bricks/, plus anything installed into VERIDIAN_HOME."""
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

    installed = list_installed_bricks()
    if installed:
        itable = Table("installed brick", "version", "contracts", "source")
        for m, rec in sorted(installed, key=lambda t: (t[0].name, t[0].version)):
            itable.add_row(
                m.name, m.version, ", ".join(sorted(m.implements)),
                (rec.source if rec else "-"),
            )
        console.print("\n[bold]VERIDIAN_HOME/bricks[/]")
        console.print(itable)


@app.command()
def which(name: str) -> None:
    """Show the ordered search roots and which one a brick name resolves through.

    Precedence: project ``./bricks`` → ``VERIDIAN_HOME/bricks`` → the repo's built-in ``bricks/``.
    First match wins. ``name`` may be pinned as ``name@version``."""
    roots = brick_roots()
    try:
        resolved = resolve_brick_ref(name, search_roots=_labelled_roots(), repo_root=repo_root())
    except (FileNotFoundError, ProtocolError) as exc:
        resolved = None
        err_console.print(f"[red]unresolved[/] {name}: {exc}")

    table = Table("#", "root", "path", "exists", "match")
    bare = name.split("@", 1)[0]
    for i, r in enumerate(roots, 1):
        versions = installed_versions(bare, [r.path]) if r.exists else {}
        hit = ""
        if resolved and resolved.origin == r.label:
            hit = f"[green]<- loads {resolved.manifest.name}@{resolved.manifest.version}[/]"
        elif versions:
            hit = f"[dim]has {', '.join(sorted(versions))}[/]"
        table.add_row(str(i), r.label, str(r.path), "yes" if r.exists else "no", hit)
    console.print(table)
    if resolved and resolved.origin == "path":
        console.print(f"[green]{name}[/] is a direct path -> {resolved.manifest.directory}")


@app.command()
def add(
    source: str = typer.Argument(..., help="Local path, git URL, or archive (.zip/.tar.gz) URL."),
    ref: str = typer.Option(None, "--ref", help="git branch/tag to clone (or use 'url#ref')."),
    path_in_repo: str = typer.Option(None, "--path", help="Subdirectory of the source that holds the brick."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the dependency-install consent prompt."),
    force: bool = typer.Option(False, "--force", help="Reinstall if this version is already present."),
    no_sdk: bool = typer.Option(False, "--no-sdk", help="Skip installing the veridian SDK into python venvs."),
    no_env: bool = typer.Option(False, "--no-env", help="Copy the brick but do not resolve its environment."),
) -> None:
    """Acquire a brick from ``source`` and install it under ``VERIDIAN_HOME/bricks/<name>/<version>/``.

    The brick is copied (never symlinked). If it declares a ``[dependencies]`` table, installing it
    runs ``uv pip install`` / ``npm install``, which execute setup code from those packages — you
    are asked to confirm unless ``--yes`` is given. Provenance (source, git commit, content hash,
    timestamp) is recorded beside the brick; see ``veridian brick inspect``.
    """
    console.print(f"source kind: [cyan]{classify_source(source)}[/]")

    def _confirm(msg: str) -> bool:
        err_console.print(f"[yellow]{msg}[/]")
        return typer.confirm("proceed")

    _ = path_in_repo  # brick add takes the single brick dir; --path reserved for parity with stack add
    try:
        result = add_brick(
            source,
            ref=ref,
            force=force,
            yes=yes,
            confirm=_confirm,
            resolve_env=not no_env,
            with_sdk=not no_sdk,
        )
    except AcquireError as exc:
        err_console.print(f"[red]add failed:[/] {exc}")
        raise typer.Exit(1)
    except (EnvironmentError_, ProtocolError) as exc:
        err_console.print(f"[red]environment resolution failed:[/] {exc}")
        raise typer.Exit(1)

    console.print(f"[green]installed[/] {result.name}@{result.version} -> {result.path}")
    console.print(f"  source:  {result.record.source} ({result.record.source_kind})")
    if result.record.git_commit:
        console.print(f"  commit:  {result.record.git_commit}")
    console.print(f"  hash:    {result.record.content_hash}")
    if result.env is not None:
        console.print(f"  env:     {result.env.action} ({result.env.interpreter or result.env.detail or '-'})")
    console.print(f"\nbind it in a stack:  {result.name.split('/')[0]} = \"{result.name}\"")


@app.command()
def remove(
    target: str = typer.Argument(..., help="Brick name, or name@version to remove one version."),
) -> None:
    """Remove an installed brick from ``VERIDIAN_HOME/bricks``. Built-in bricks are never touched."""
    name, _, version = target.partition("@")
    try:
        removed = remove_brick(name, version or None)
    except AcquireError as exc:
        err_console.print(f"[red]remove failed:[/] {exc}")
        raise typer.Exit(1)
    for p in removed:
        console.print(f"[green]removed[/] {p}")


@app.command()
def inspect(ref: str) -> None:
    """Show a brick's manifest, provenance, and trust assessment."""
    m = _resolve(ref)
    console.print(f"[bold]{m.name}[/] {m.version} — {m.description or ''}")
    console.print(f"runtime: {m.runtime}   protocol: {m.protocol}")
    if m.requires_veridian:
        console.print(f"requires Veridian: {m.requires_veridian}")
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

    rec = InstallRecord.from_dir(m.directory)
    if rec is not None:
        console.print("\n[bold]provenance[/] [dim](install record, not a signature)[/]")
        console.print(f"  source:    {rec.source} ({rec.source_kind})")
        if rec.git_commit:
            console.print(f"  commit:    {rec.git_commit}")
        console.print(f"  installed: {rec.installed_at}  (Veridian {rec.veridian_version})")
        console.print(f"  hash:      {rec.content_hash}")
        current = None
        try:
            from veridian.plugin_runtime.acquire import content_hash as _ch

            current = _ch(m.directory)
        except Exception:  # noqa: BLE001
            pass
        if current and current != rec.content_hash:
            err_console.print(
                f"  [yellow]warning[/] on-disk content hash {current} != recorded {rec.content_hash}"
            )
    else:
        console.print("\n[dim]provenance: none (not installed via `veridian brick add`)[/]")

    a = assess(m)
    console.print(f"\ntrust: [bold]{a.level.name}[/] — {a.advisory}")


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
