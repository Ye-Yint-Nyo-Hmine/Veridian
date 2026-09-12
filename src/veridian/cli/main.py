"""The ``veridian`` CLI.

Bare ``veridian`` (i.e. ``uv run veridian``) starts the interactive session. Everything else is a
subcommand: ``run`` is the one-shot form of the same agent loop; ``brick``, ``stack``, ``doctor``,
and ``protocol`` are unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from veridian.cli.commands import brick_cmds, doctor_cmds, protocol_cmds, run_cmds, stack_cmds

app = typer.Typer(
    name="veridian",
    help="An open-source, plugin-native runtime for coding agents. Run bare for an interactive session.",
    add_completion=False,
    no_args_is_help=False,
)

app.command("run")(run_cmds.run)
app.command("doctor")(doctor_cmds.doctor)
app.add_typer(stack_cmds.app, name="stack")
app.add_typer(brick_cmds.app, name="brick")
app.add_typer(protocol_cmds.app, name="protocol")


def _version_lines() -> list[str]:
    from veridian import __version__
    from veridian._paths import install_mode, veridian_root

    lines = [f"veridian {__version__}"]
    try:
        root = veridian_root()
    except RuntimeError as exc:  # a bad VERIDIAN_ROOT should be visible, not hidden
        lines.append(str(exc))
        return lines
    lines.append(f"root {root}  ({install_mode()})" if root else "root not found")
    return lines


def _version_cb(value: bool) -> None:
    if value:
        for line in _version_lines():
            typer.echo(line)
        raise typer.Exit()


@app.command()
def version() -> None:
    """Print the Veridian version and where this install lives."""
    for line in _version_lines():
        typer.echo(line)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", callback=_version_cb, is_eager=True, help="Print the version and exit."
    ),
    stack: str = typer.Option(
        None, "--stack", "-s",
        help="Stack file path or installed stack name (default: your configured stack, else the built-in one).",
    ),
    workspace: Path = typer.Option(
        None, "--workspace", "-w", help="Workspace root for the interactive session (default: cwd)."
    ),
    max_iterations: int = typer.Option(12, help="Cap on orchestrator loop iterations per goal."),
    resume: str = typer.Option(
        None, "--resume",
        help="Resume a session by id; give --resume with no id to pick one from a list.",
    ),
) -> None:
    """Start the interactive session when no subcommand is given."""
    if ctx.invoked_subcommand is not None:
        return
    from veridian.cli.interactive import start_interactive

    start_interactive(
        stack=stack, workspace=workspace, max_iterations=max_iterations, resume=resume
    )


def run(argv: list[str] | None = None) -> None:
    """Console-script entry point. Rewrites a bare ``--resume`` (no id) to ``--resume=`` so the
    option can mean both "resume this id" and "let me pick one" — Typer options otherwise always
    require a value."""
    # Provider keys the user asked us to remember live here; bricks read them from the environment
    # through their manifest allowlist, so they have to be in place before any kernel starts.
    from veridian.cli.userconfig import load_env_file

    load_env_file()

    args = list(sys.argv[1:] if argv is None else argv)
    fixed: list[str] = []
    for i, tok in enumerate(args):
        if tok == "--resume" and (i + 1 >= len(args) or args[i + 1].startswith("-")):
            fixed.append("--resume=")
        else:
            fixed.append(tok)
    app(fixed)


if __name__ == "__main__":
    run()
