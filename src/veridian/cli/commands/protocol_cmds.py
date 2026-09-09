"""`veridian protocol ...`"""

from __future__ import annotations

import json

import typer

from veridian.cli._common import console
from veridian.contracts._schemas import all_schema_files, load_schema, schema_dir

app = typer.Typer(no_args_is_help=True, help="Inspect the wire protocol schemas.")


@app.command()
def schema(name: str = typer.Argument(None, help="Schema file name, e.g. protocol/inference.schema.json")) -> None:
    """Print a JSON Schema, or list them all when no name is given."""
    if not name:
        base = schema_dir()
        for f in all_schema_files():
            console.print(str(f.relative_to(base)))
        return
    candidates = [name, f"protocol/{name}", f"{name}.schema.json", f"protocol/{name}.schema.json"]
    for cand in candidates:
        try:
            doc = load_schema(cand)
        except FileNotFoundError:
            continue
        console.print_json(json.dumps(doc))
        return
    raise typer.BadParameter(f"no schema matching {name!r}")
