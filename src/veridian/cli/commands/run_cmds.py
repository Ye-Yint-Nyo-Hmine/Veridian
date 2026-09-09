"""`veridian run <goal>`"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from veridian.cli._common import console, default_stack, err_console
from veridian.contracts.errors import CONTRACT_NOT_BOUND, ProtocolError
from veridian.kernel import Kernel, load_stack
from veridian.kernel.errors import StackConfigError


def run(
    goal: str = typer.Argument(..., help="What you want the agent to do."),
    stack: Path = typer.Option(None, "--stack", "-s", help="Stack file (default: stacks/default.toml)."),
    workspace: Path = typer.Option(Path.cwd(), "--workspace", "-w", help="Workspace root."),
    max_iterations: int = typer.Option(12, help="Cap on orchestrator loop iterations."),
) -> None:
    """Run the agent loop for a goal, through whichever orchestrator the stack binds."""
    stack_path = stack or default_stack()
    try:
        resolved = load_stack(stack_path)
    except StackConfigError as exc:
        err_console.print(f"[red]stack error:[/] {exc}")
        raise typer.Exit(1)

    if resolved.binding_for("orchestrator") is None:
        err_console.print(f"[red]stack {resolved.name!r} binds no orchestrator[/]")
        raise typer.Exit(1)

    asyncio.run(_run(resolved, goal, workspace.resolve(), max_iterations))


async def _run(resolved, goal: str, workspace: Path, max_iterations: int) -> None:
    kernel = Kernel(resolved, workspace_root=workspace)

    kernel.events.subscribe("brick.crashed", lambda e: err_console.print(f"[red]brick crashed:[/] {e.payload}"))
    kernel.events.subscribe(
        "brick.log", lambda e: console.print(f"[dim]{e.source}: {e.payload.get('message', '')}[/]")
    )

    console.print(f"[bold]stack[/] {resolved.name}   [bold]goal[/] {goal}")
    await kernel.start()
    try:
        stream = await kernel.call_stream(
            "orchestrator",
            "run",
            {"goal": goal, "workspace_root": str(workspace), "limits": {"max_iterations": max_iterations}},
        )
        async for delta in stream:
            _render(delta.get("event", "?"), delta.get("data", {}))
        result = await stream.result()
        console.print()
        colour = {"completed": "green", "failed": "red", "max_iterations": "yellow"}.get(result["status"], "white")
        console.print(f"[{colour}]{result['status']}[/] after {result['iterations']} iteration(s)")
        console.print(result["summary"])
    except ProtocolError as exc:
        if exc.code == CONTRACT_NOT_BOUND:
            err_console.print(f"[red]{exc.message}[/]")
        else:
            err_console.print(f"[red]run failed:[/] {exc}")
        raise typer.Exit(1)
    finally:
        await kernel.stop()


def _render(event: str, data: dict) -> None:
    if event == "log":
        console.print(f"[dim]· {data.get('message', '')}[/]")
    elif event == "step":
        if data.get("kind") == "plan":
            console.print("[cyan]plan:[/] " + " → ".join(data.get("steps", [])))
        elif data.get("kind") == "active":
            console.print(f"[cyan]step:[/] {data.get('description', '')}")
    elif event == "tool":
        if "output" in data:
            tag = "[red]tool error[/]" if data.get("is_error") else "[green]tool ok[/]"
            console.print(f"  {tag} {data.get('name')}: {str(data.get('output',''))[:200]}")
        else:
            console.print(f"  [yellow]tool call[/] {data.get('name')} {data.get('input')}")
    elif event == "message":
        console.print(f"[white]{data.get('text', '')}[/]")
