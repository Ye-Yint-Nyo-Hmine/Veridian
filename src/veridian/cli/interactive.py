"""The interactive session — what ``uv run veridian`` starts.

One kernel is started lazily on the first goal and reused for the whole session. Each goal is one
``orchestrator.run`` streamed through the :class:`Renderer`. The event loop is driven one turn at a
time so that a Ctrl-C during a run unwinds only that turn: the loop catches ``KeyboardInterrupt``
out of ``run_until_complete``, cancels the turn task, and returns to the prompt with the kernel
still up.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from veridian import __version__
from veridian.cli._common import console, default_stack, err_console
from veridian.cli.ui import Renderer
from veridian.contracts.errors import CONTRACT_NOT_BOUND, ProtocolError
from veridian.kernel import Kernel, load_stack
from veridian.kernel.errors import StackConfigError
from veridian.plugin_runtime.manifest import UnresolvedEnvironment

_HELP = """commands:
  /help              show this
  /stack             the active stack, bindings, and policy grant
  /workspace         the workspace root
  /exit, /quit       leave (Ctrl-D also works)
Anything else is sent to the orchestrator as a goal."""


def start_interactive(
    stack: Path | None = None,
    workspace: Path | None = None,
    max_iterations: int = 12,
) -> None:
    """Load a stack, then run the read → run → render loop until EOF or /exit."""
    stack_path = stack or default_stack()
    try:
        resolved = load_stack(stack_path)
    except StackConfigError as exc:
        err_console.print(f"[v.err]stack error:[/] {exc}")
        raise typer.Exit(1)
    if resolved.binding_for("orchestrator") is None:
        err_console.print(f"[v.err]stack {resolved.name!r} binds no orchestrator[/]")
        raise typer.Exit(1)

    ws = (workspace or Path.cwd()).resolve()
    renderer = Renderer(console, err_console)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    kernel = Kernel(resolved, workspace_root=ws)
    _wire_events(kernel, renderer)

    renderer.session_start(stack=resolved, workspace=ws, version=__version__)
    try:
        _repl(loop, kernel, resolved, ws, max_iterations, renderer)
    finally:
        renderer.session_end()
        try:
            if kernel._started:  # noqa: SLF001 - idempotent, private flag is the only signal
                loop.run_until_complete(kernel.stop())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _repl(loop, kernel, resolved, ws, max_iterations, renderer: Renderer) -> None:
    while True:
        try:
            line = renderer.read_prompt()
        except (EOFError, KeyboardInterrupt):
            renderer.newline()
            return

        goal = line.strip()
        if not goal:
            continue
        if goal in ("/exit", "/quit"):
            return
        if goal == "/help":
            console.print(_HELP)
            continue
        if goal == "/stack":
            _show_stack(resolved)
            continue
        if goal == "/workspace":
            console.print(str(ws))
            continue
        if goal.startswith("/"):
            renderer.error(f"unknown command {goal!r} — /help for commands")
            continue

        turn = loop.create_task(_run_goal(kernel, goal, ws, max_iterations, renderer))
        try:
            loop.run_until_complete(turn)
        except KeyboardInterrupt:
            turn.cancel()
            try:
                loop.run_until_complete(turn)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - turn already reported
                pass
            renderer.run_interrupted()
            # SEAM — Milestone 2 A4: when the protocol gains a cancel message, send it to the
            # orchestrator here so the brick stops instead of running to completion detached.


async def _run_goal(kernel: Kernel, goal: str, ws: Path, max_iter: int, renderer: Renderer) -> None:
    if not kernel._started:  # noqa: SLF001
        renderer.info("starting bricks…")
        try:
            await kernel.start()
        except (ProtocolError, UnresolvedEnvironment) as exc:
            renderer.error(f"stack failed to start: {exc}")
            return

    renderer.run_begin(goal)
    try:
        stream = await kernel.call_stream(
            "orchestrator",
            "run",
            {"goal": goal, "workspace_root": str(ws), "limits": {"max_iterations": max_iter}},
        )
        async for delta in stream:
            renderer.delta(delta.get("event", "?"), delta.get("data", {}))
        renderer.run_end(await stream.result())
    except asyncio.CancelledError:
        renderer.clear_activity()
        raise
    except ProtocolError as exc:
        if exc.code == CONTRACT_NOT_BOUND:
            renderer.error(exc.message)
        else:
            renderer.error(f"run failed: {exc}")


def _wire_events(kernel: Kernel, renderer: Renderer) -> None:
    kernel.events.subscribe(
        "brick.crashed", lambda e: renderer.error(f"brick crashed: {e.payload}")
    )
    kernel.events.subscribe(
        "brick.restarting", lambda e: renderer.kernel_note(f"restarting {e.source}")
    )

    def _on_log(e) -> None:
        if e.payload.get("level") in ("warning", "error"):
            renderer.kernel_note(f"{e.source}: {e.payload.get('message', '')}")

    kernel.events.subscribe("brick.log", _on_log)


def _show_stack(resolved) -> None:
    console.print(f"[v.user]{resolved.name}[/] — {resolved.description or '(no description)'}")
    for b in resolved.active():
        console.print(f"  [v.meta]{b.contract:<13}[/] {b.manifest.name}")
    console.print(f"  [v.meta]policy grant [/] {', '.join(sorted(resolved.policy.grant)) or '-'}")


__all__ = ["start_interactive"]
