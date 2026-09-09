"""CLI entry point. Commands are wired up in Phase 6."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="veridian",
    help="An open-source, plugin-native runtime for coding agents.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """Veridian runtime CLI."""


@app.command()
def version() -> None:
    """Print the Veridian version."""
    from veridian import __version__

    typer.echo(__version__)


if __name__ == "__main__":
    app()
