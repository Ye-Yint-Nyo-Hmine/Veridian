"""The ``veridian`` CLI."""

from __future__ import annotations

import typer

from veridian.cli.commands import brick_cmds, doctor_cmds, protocol_cmds, run_cmds, stack_cmds

app = typer.Typer(
    name="veridian",
    help="An open-source, plugin-native runtime for coding agents.",
    no_args_is_help=True,
    add_completion=False,
)

app.command("run")(run_cmds.run)
app.command("doctor")(doctor_cmds.doctor)
app.add_typer(stack_cmds.app, name="stack")
app.add_typer(brick_cmds.app, name="brick")
app.add_typer(protocol_cmds.app, name="protocol")


@app.command()
def version() -> None:
    """Print the Veridian version."""
    from veridian import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
