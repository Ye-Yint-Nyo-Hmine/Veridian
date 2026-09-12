"""The interactive session — what ``uv run veridian`` starts.

One kernel is started lazily on the first goal and reused for the whole session. Each goal is one
``orchestrator.run`` streamed through the :class:`Renderer`. The event loop is driven one turn at a
time so that a Ctrl-C during a run unwinds only that turn: the loop catches ``KeyboardInterrupt``
out of ``run_until_complete``, cancels the turn task, sends ``$/cancel`` to the orchestrator via
the live stream handle, and returns to the prompt with the kernel still up. The kernel cascades
that cancel down every ``host.contract.call`` the orchestrator made (protocol §5.1), so a Ctrl-C
mid-generation also stops the downstream model call.

Slash commands are not handled here — they live in :mod:`veridian.cli.repl_commands`, one registry
that ``/help`` renders from. This module owns turn machinery: the read loop, ``@`` mention
resolution, and the Ctrl-C / ``$/cancel`` wiring.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from veridian import __version__
from veridian.cli import mentions as mention
from veridian.cli._common import (
    console,
    default_stack,
    describe_run_failure,
    err_console,
    missing_provider_env,
    model_name,
    stacks_root,
)
from veridian.cli.mode import DEFAULT_MODE, MODES, withheld_capabilities
from veridian.cli.onboarding import run_onboarding, should_run
from veridian.cli.repl_commands import (
    ReplContext,
    _override_binding_config,
    _SessionState,
    _wire_events,
    lookup,
    render_help,
)
from veridian.cli.ui import Renderer, current_branch, select
from veridian.contracts.errors import CONTRACT_NOT_BOUND, ProtocolError
from veridian.kernel import Kernel, load_stack, resolve_stack_ref
from veridian.kernel.errors import StackConfigError
from veridian.plugin_runtime.manifest import UnresolvedEnvironment
from veridian.session_store import SessionStore, list_recent
from veridian.session_store import load as load_session
from veridian.session_store import new_session_id


def start_interactive(
    stack: str | Path | None = None,
    workspace: Path | None = None,
    max_iterations: int = 12,
    resume: str | None = None,
) -> None:
    """Load a stack, then run the read → run → render loop until EOF or /exit.

    ``resume`` is ``None`` for a fresh session, ``""`` to pick a session to resume from a list, or
    a session id to restore directly.
    """
    resumed = _pick_resume(resume)
    if resume is not None and resumed is None:
        # a bare --resume with nothing to pick, or a cancelled pick
        return

    if resumed is not None:
        stack = stack or resumed.stack
        if workspace is None:
            workspace = Path(resumed.workspace)
            if not workspace.is_dir():
                err_console.print(
                    f"[v.err]session {resumed.id[:8]} recorded workspace {resumed.workspace!r}, "
                    f"which no longer exists[/]"
                )
                raise typer.Exit(1)

    if resumed is None and should_run(stack if isinstance(stack, str) else None):
        run_onboarding(console, err_console, builtin_stacks=stacks_root())

    try:
        stack_path = resolve_stack_ref(stack) if stack else default_stack()
        resolved = load_stack(stack_path)
    except StackConfigError as exc:
        err_console.print(f"[v.err]stack error:[/] {exc}")
        raise typer.Exit(1)
    if resolved.binding_for("orchestrator") is None:
        err_console.print(f"[v.err]stack {resolved.name!r} binds no orchestrator[/]")
        raise typer.Exit(1)

    # Say this before the session starts rather than letting the first goal die inside the brick.
    # Not fatal: /stack and /model can fix it from the prompt, and the kernel runs regardless.
    for var in missing_provider_env(resolved):
        err_console.print(
            f"[v.warn]{var} is not set[/] — goals will fail until it is. "
            f"Set it, or use [v.meta]/stack[/] to pick a local model."
        )

    ws = (workspace or Path.cwd()).resolve()
    session_id = resumed.id if resumed else new_session_id()
    start_mode = resumed.mode if (resumed and resumed.mode in MODES) else DEFAULT_MODE

    # Unify session identity: the id on screen is the id the conversation brick keys history
    # under, so resuming restores the conversation, not just this record. Push it (and, on a
    # resume, the last model) into the relevant bindings before the kernel is built.
    _override_binding_config(resolved, "orchestrator", {"session_id": session_id})
    model = model_name(resolved)
    if resumed and resumed.model:
        _override_binding_config(resolved, "inference", {"model": resumed.model})
        model = resumed.model

    renderer = Renderer(console, err_console)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    kernel = Kernel(resolved, workspace_root=ws)
    _wire_events(kernel, renderer)

    renderer.session_start(
        stack=resolved,
        workspace=ws,
        version=__version__,
        session_id=session_id,
        model=model,
        mode=start_mode,
    )
    if resumed:
        renderer.info(f"resumed session {session_id[:8]} — conversation history will load on the next goal")

    store = _open_store(session_id, ws, resolved.name, model, start_mode, fresh=resumed is None)

    live_kernel = kernel
    try:
        live_kernel = _repl(
            loop, kernel, resolved, ws, max_iterations, renderer,
            session_id=session_id, session_store=store, start_mode=start_mode,
        ) or kernel
    finally:
        if store is not None:
            try:
                store.touch()
            except OSError:
                pass
        renderer.session_end()
        try:
            # live_kernel, not the boot kernel — a /stack switch swaps in a new one and stops the
            # old one as it goes, so this is the only kernel still up.
            if live_kernel._started:  # noqa: SLF001 - idempotent, private flag is the only signal
                loop.run_until_complete(live_kernel.stop())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _pick_resume(resume: str | None):
    """Resolve the ``resume`` argument to a :class:`SessionMeta` or ``None``."""
    if resume is None:
        return None
    if resume == "":
        metas = list_recent()
        if not metas:
            err_console.print("[v.meta]no resumable sessions[/]")
            return None
        labels = [
            f"{m.id[:8]}  {m.stack:<16}  {m.model:<18}  {m.summary or '—'}  ({m.last_activity_at})"
            for m in metas
        ]
        idx = select(labels, prompt="resume", console=console, err_console=err_console)
        return metas[idx] if idx is not None else None
    meta = load_session(resume)
    if meta is None:
        err_console.print(f"[v.err]no session {resume!r} under ~/.veridian/sessions[/]")
        raise typer.Exit(1)
    return meta


def _open_store(session_id, ws, stack_name, model, mode, *, fresh: bool):
    """Create (fresh) or reopen (resume) the on-disk session record. A home that cannot be written
    is not fatal — the session runs, it just is not resumable."""
    try:
        if fresh:
            return SessionStore.create(
                session_id, workspace=ws, stack=stack_name, model=model, mode=mode
            )
        meta = load_session(session_id)
        store = SessionStore(meta) if meta else SessionStore.create(
            session_id, workspace=ws, stack=stack_name, model=model, mode=mode
        )
        store.update(workspace=str(ws), stack=stack_name, model=model, mode=mode)
        return store
    except OSError:
        return None


def _repl(
    loop,
    kernel,
    resolved,
    ws,
    max_iterations,
    renderer: Renderer,
    *,
    session_id: str | None = None,
    session_store=None,
    start_mode: str | None = None,
) -> Kernel:
    """Run the read → run → render loop. Returns the kernel that is live at exit — which is *not*
    the boot kernel if a ``/stack`` switch replaced it — so the caller tears the right one down."""
    state = _SessionState(start_mode or DEFAULT_MODE)
    ctx = ReplContext(
        loop=loop,
        kernel=kernel,
        resolved=resolved,
        workspace=Path(ws),
        renderer=renderer,
        state=state,
        max_iterations=max_iterations,
        model=_safe_model(resolved),
        session_id=session_id,
        session_store=session_store,
    )
    while True:
        if hasattr(renderer, "footer"):
            renderer.footer(model=ctx.model, branch=current_branch(Path(ws)), mode=state.mode)
        try:
            line = renderer.read_prompt()
        except (EOFError, KeyboardInterrupt):
            renderer.newline()
            return ctx.kernel

        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("/"):
            token, _, rest = stripped.partition(" ")
            cmd = lookup(token)
            if cmd is None:
                renderer.error(f"unknown command {token!r} — /help for commands")
                continue
            cmd.handler(ctx, rest.strip())
            if state.should_exit:
                return ctx.kernel
            continue

        found, merr = mention.resolve(line, ctx.workspace)
        if merr:
            renderer.error(f"@ mention: {merr}")
            continue
        goal = mention.build_turn(line, found) if found else stripped
        if found:
            ctx.mentions.extend(found)
            for note in mention.notes(found):
                renderer.info(note)

        active = _ActiveTurn()
        turn = loop.create_task(
            # ctx.kernel, not the boot kernel — a /stack switch may have replaced it.
            _run_goal(ctx.kernel, goal, ws, max_iterations, renderer, active, state, session_store)
        )
        try:
            loop.run_until_complete(turn)
        except KeyboardInterrupt:
            turn.cancel()
            try:
                loop.run_until_complete(turn)
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - turn already reported
                pass
            # A4: tell the orchestrator to stop. One cancel on the live stream; the kernel
            # cascades it down every host.contract.call the run made.
            if active.stream is not None:
                try:
                    loop.run_until_complete(active.stream.cancel())
                except Exception:  # noqa: BLE001 - orchestrator may already be gone
                    pass
            renderer.run_interrupted()


def _safe_model(resolved) -> str:
    try:
        return model_name(resolved)
    except Exception:  # noqa: BLE001 - a bare object() in tests; the footer shows "?"
        return "?"


class _ActiveTurn:
    """Mutable handle the REPL shares with the running turn so a Ctrl-C can reach the live stream
    and send ``$/cancel``."""

    def __init__(self) -> None:
        self.stream = None


async def _run_goal(
    kernel: Kernel,
    goal: str,
    ws: Path,
    max_iter: int,
    renderer: Renderer,
    active: _ActiveTurn,
    state: _SessionState,
    session_store=None,
) -> None:
    if not kernel._started:  # noqa: SLF001
        renderer.info("starting bricks…")
        try:
            await kernel.start()
        except (ProtocolError, UnresolvedEnvironment) as exc:
            renderer.error(f"stack failed to start: {exc}")
            return

    # apply the mode's capability restriction to the freshly-started kernel once; later mode
    # changes go through the /mode handler. Re-applying every turn would recompute effective
    # capabilities from the manifest and drop anything a brick was granted dynamically.
    if not state.caps_applied:
        state.caps_applied = True
        kernel.restrict_capabilities(withheld_capabilities(state.mode))
    if not state.counts_shown:
        state.counts_shown = True
        try:
            listing = await kernel.call("tools", "list", {})
            renderer.tool_count(len(listing.get("tools", [])))
        except ProtocolError:
            pass  # no tools brick bound — nothing to count

    renderer.run_begin(goal)
    try:
        stream = await kernel.call_stream(
            "orchestrator",
            "run",
            {"goal": goal, "workspace_root": str(ws), "limits": {"max_iterations": max_iter}},
        )
        active.stream = stream
        async for delta in stream:
            renderer.delta(delta.get("event", "?"), delta.get("data", {}))
        result = await stream.result()
        renderer.run_end(result)
        if session_store is not None:
            try:
                session_store.set_summary(result.get("summary", ""))
            except OSError:
                pass
    except asyncio.CancelledError:
        renderer.clear_activity()
        raise
    except ProtocolError as exc:
        if exc.code == CONTRACT_NOT_BOUND:
            renderer.error(exc.message)
        else:
            renderer.error(describe_run_failure(exc))


__all__ = ["start_interactive", "render_help"]
