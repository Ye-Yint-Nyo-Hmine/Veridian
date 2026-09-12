"""``veridian run <goal>`` — the one-shot form of the interactive session.

Same stack, same kernel, same rendering as ``uv run veridian``; it just takes one goal on the
command line, runs it, and exits with a status-derived code. Kept as a compatibility path and for
scripting.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import typer

from veridian import __version__
from veridian.cli._common import (
    console,
    default_stack,
    describe_run_failure,
    err_console,
    missing_provider_env,
    model_name,
)
from veridian.cli.ui import Renderer
from veridian.contracts.errors import CONTRACT_NOT_BOUND, ProtocolError
from veridian.kernel import Kernel, load_stack, resolve_stack_ref
from veridian.kernel.errors import StackConfigError
from veridian.plugin_runtime.manifest import UnresolvedEnvironment

_EXIT_CODE = {"completed": 0}  # anything else -> 1


def run(
    goal: str = typer.Argument(..., help="What you want the agent to do."),
    stack: str = typer.Option(None, "--stack", "-s", help="Stack file path or installed stack name (default: your configured stack, else the built-in one)."),
    workspace: Path = typer.Option(None, "--workspace", "-w", help="Workspace root (default: cwd)."),
    max_iterations: int = typer.Option(12, help="Cap on orchestrator loop iterations."),
) -> None:
    """Run the agent loop for a single goal, through whichever orchestrator the stack binds."""
    try:
        stack_path = resolve_stack_ref(stack) if stack else default_stack()
        resolved = load_stack(stack_path)
    except StackConfigError as exc:
        err_console.print(f"[v.err]stack error:[/] {exc}")
        raise typer.Exit(1)

    if resolved.binding_for("orchestrator") is None:
        err_console.print(f"[v.err]stack {resolved.name!r} binds no orchestrator[/]")
        raise typer.Exit(1)

    # A one-shot has no prompt to recover at, so a missing key is fatal here rather than a warning.
    missing = missing_provider_env(resolved)
    if missing:
        err_console.print(
            f"[v.err]{' and '.join(missing)} not set[/] — `veridian run` needs an inference provider. "
            f"Set it, or pass --stack with a local model."
        )
        raise typer.Exit(1)

    renderer = Renderer(console, err_console)
    try:
        code = asyncio.run(_run(resolved, goal, (workspace or Path.cwd()).resolve(), max_iterations, renderer))
    except KeyboardInterrupt:
        renderer.run_interrupted()
        raise typer.Exit(130)
    raise typer.Exit(code)


async def _run(resolved, goal: str, workspace: Path, max_iterations: int, renderer: Renderer) -> int:
    kernel = Kernel(resolved, workspace_root=workspace)
    kernel.events.subscribe("brick.crashed", lambda e: renderer.error(f"brick crashed: {e.payload}"))

    def _on_log(e) -> None:
        if e.payload.get("level") in ("warning", "error"):
            renderer.kernel_note(f"{e.source}: {e.payload.get('message', '')}")

    kernel.events.subscribe("brick.log", _on_log)

    # one-shot: no mode cycling, so the session runs at 'auto' (the stack's full grant).
    renderer.session_start(
        stack=resolved,
        workspace=workspace,
        version=__version__,
        session_id=uuid.uuid4().hex[:8],
        model=model_name(resolved),
        mode="auto",
    )
    try:
        await kernel.start()
    except (ProtocolError, UnresolvedEnvironment) as exc:
        await kernel.stop()
        renderer.error(f"stack failed to start: {exc}")
        return 1

    renderer.run_begin(goal)
    try:
        stream = await kernel.call_stream(
            "orchestrator",
            "run",
            {"goal": goal, "workspace_root": str(workspace), "limits": {"max_iterations": max_iterations}},
        )
        async for delta in stream:
            renderer.delta(delta.get("event", "?"), delta.get("data", {}))
        result = await stream.result()
        renderer.run_end(result)
        return _EXIT_CODE.get(result.get("status", ""), 1)
    except ProtocolError as exc:
        renderer.error(exc.message if exc.code == CONTRACT_NOT_BOUND else describe_run_failure(exc))
        return 1
    finally:
        await kernel.stop()
