"""The interactive session's slash-command registry.

One table, one source of truth. Every command carries its own help text and argument spec, and
``/help`` renders from the table — so the help can never list a command the loop does not accept,
or miss one it does. ``interactive._repl`` looks a token up here and calls its handler; anything
that is not a registered command is sent to the orchestrator as a goal.

A handler takes the :class:`ReplContext` and the rest of the input line (everything after the
command token, already stripped) and drives the session through the renderer. Handlers are
synchronous; the ones that need the kernel drive it with ``ctx.loop.run_until_complete``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from veridian import __version__
from veridian.cli._common import console, model_name
from veridian.cli.commands.stack_cmds import discover_stacks
from veridian.cli.mode import PLAN_MODE_NOTE, next_mode, withheld_capabilities
from veridian.cli.ui.renderer import usage_cell
from veridian.cli.ui.selector import select
from veridian.contracts.errors import (
    CONTRACT_NOT_BOUND,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    UNSUPPORTED_METHOD,
    ProtocolError,
)
from veridian.kernel import Kernel, load_stack, resolve_stack_ref
from veridian.kernel.errors import StackConfigError

if TYPE_CHECKING:  # pragma: no cover
    from veridian.cli.mentions import Mention
    from veridian.cli.ui import Renderer
    from veridian.kernel import ResolvedStack
    from veridian.session_store import SessionStore


class _SessionState:
    """REPL-scoped state that outlives a single turn: the current mode, whether the one-shot
    tool count / capability restriction have been applied, and whether the loop should stop."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.counts_shown = False
        self.caps_applied = False
        self.should_exit = False


@dataclass
class ReplContext:
    """The mutable session handle handed to every command handler."""

    loop: Any
    kernel: "Kernel"
    resolved: "ResolvedStack"
    workspace: Path
    renderer: "Renderer"
    state: _SessionState
    max_iterations: int
    model: str
    session_id: str | None = None
    session_store: "SessionStore | None" = None
    mentions: list["Mention"] = field(default_factory=list)


Handler = Callable[["ReplContext", str], None]


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    handler: Handler
    aliases: tuple[str, ...] = ()
    usage: str | None = None

    @property
    def tokens(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    @property
    def left(self) -> str:
        head = ", ".join(self.tokens)
        return f"{head} {self.usage}" if self.usage else head


# -- handlers -------------------------------------------------------------------


def _ensure_kernel(ctx: "ReplContext") -> bool:
    """Bring the kernel up if a command needs it before the first goal has. Returns ``False`` (and
    reports) if the stack fails to start."""
    if getattr(ctx.kernel, "_started", True):
        return True
    ctx.renderer.info("starting bricks…")
    try:
        ctx.loop.run_until_complete(ctx.kernel.start())
        return True
    except Exception as exc:  # noqa: BLE001 - surfaced to the user, session stays up
        ctx.renderer.error(f"stack failed to start: {exc}")
        return False


def show_stack(resolved: "ResolvedStack") -> None:
    """The active stack, its bindings, and the policy grant. Shared by ``/stack`` and
    ``/context``."""
    console.print(f"[v.user]{resolved.name}[/] — {resolved.description or '(no description)'}")
    for b in resolved.active():
        console.print(f"  [v.meta]{b.contract:<13}[/] {b.manifest.name}")
    console.print(f"  [v.meta]policy grant [/] {', '.join(sorted(resolved.policy.grant)) or '-'}")


def cmd_help(ctx: "ReplContext", _args: str) -> None:
    console.print(render_help())


def cmd_exit(ctx: "ReplContext", _args: str) -> None:
    ctx.state.should_exit = True


def _wire_events(kernel: "Kernel", renderer: "Renderer") -> None:
    """Forward brick crash / restart / warning events to the renderer. Applied to the boot kernel
    and to every kernel a ``/stack`` switch brings up."""
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


def _override_binding_config(resolved: "ResolvedStack", contract: str, extra: dict) -> None:
    """Merge ``extra`` into the config of ``resolved``'s ``contract`` binding, in place."""
    for i, b in enumerate(resolved.bindings):
        if b.contract == contract:
            resolved.bindings[i] = dataclasses.replace(b, config={**b.config, **extra})
            return


def cmd_stack(ctx: "ReplContext", args: str) -> None:
    """Bare ``/stack`` shows the active stack; ``/stack list`` (or ``/stack`` before the kernel is
    up) offers the numbered picker; ``/stack <name>`` switches straight to a stack."""
    arg = args.strip()
    kernel_up = bool(getattr(ctx.kernel, "_started", False))
    if arg == "list" or (not arg and not kernel_up):
        _pick_and_switch_stack(ctx)
        return
    if not arg:
        show_stack(ctx.resolved)
        return
    _switch_stack(ctx, arg)


def _pick_and_switch_stack(ctx: "ReplContext") -> None:
    """List every discoverable stack through the shared numbered selector, marking the active one,
    and switch to whatever the user picks."""
    r = ctx.renderer
    entries = discover_stacks()
    if not entries:
        r.info("no stacks discovered")
        return
    active = getattr(ctx.resolved, "path", None)
    labels = []
    for e in entries:
        mark = "* " if e.path == active else "  "
        if e.error is not None:
            labels.append(f"{mark}{e.name} — [invalid: {e.error}]")
        else:
            binds = ", ".join(e.bindings) or "-"
            labels.append(f"{mark}{e.name} — {binds}  ({e.source})")
    idx = select(labels, prompt="stack", console=console, err_console=r.err)
    if idx is None:
        r.info("no change")
        return
    chosen = entries[idx]
    if chosen.path == active:
        r.info(f"already on {chosen.name}")
        return
    if chosen.error is not None:
        r.error(f"stack {chosen.name!r} does not load: {chosen.error}")
        return
    _switch_stack(ctx, str(chosen.path))


def _switch_stack(ctx: "ReplContext", ref: str) -> None:
    """Tear down the running stack and bring up ``ref`` in its place, keeping the session id,
    conversation, mode, and workspace.

    The target is fully resolved and validated *before* anything is stopped, and the new kernel is
    started *before* the old one is torn down — so a target that will not load or will not start
    leaves the current session exactly as it was.
    """
    r = ctx.renderer

    # 1. Resolve + load the target. Nothing is touched if this fails.
    try:
        target = load_stack(resolve_stack_ref(ref))
    except StackConfigError as exc:
        r.error(f"stack {ref!r} did not load — staying on {ctx.resolved.name}: {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - any resolution failure must leave the session up
        r.error(f"stack {ref!r} did not load — staying on {ctx.resolved.name}: {exc}")
        return

    if target.path == getattr(ctx.resolved, "path", None):
        r.info(f"already on {target.name}")
        return
    if target.binding_for("orchestrator") is None:
        r.error(f"stack {target.name!r} binds no orchestrator — not switching")
        return
    if target.binding_for("inference") is None:
        r.error(
            f"stack {target.name!r} ships with no inference binding — "
            f"add one before switching to it"
        )
        return

    # 2. Carry the session identity into the new orchestrator binding so the conversation the id
    #    keys is the same one after the swap (mirrors interactive boot).
    _override_binding_config(target, "orchestrator", {"session_id": ctx.session_id or "default"})

    old_kernel = ctx.kernel
    old_started = bool(getattr(old_kernel, "_started", False))
    new_kernel = Kernel(target, workspace_root=ctx.workspace)
    _wire_events(new_kernel, r)

    # 3. If the old kernel is running, start the new one first and only tear the old one down on a
    #    clean start. If it is not running, there is nothing to start or roll back yet.
    if old_started:
        try:
            ctx.loop.run_until_complete(new_kernel.start())
        except Exception as exc:  # noqa: BLE001 - a failed start must leave the old kernel serving
            try:
                ctx.loop.run_until_complete(new_kernel.stop())
            except Exception:  # noqa: BLE001
                pass
            r.error(f"stack {target.name!r} failed to start — staying on {ctx.resolved.name}: {exc}")
            return
        # reapply the session mode's withheld capabilities to the fresh kernel (cmd_mode does the
        # same on a mode change).
        new_kernel.restrict_capabilities(withheld_capabilities(ctx.state.mode))
        ctx.state.caps_applied = True
        try:
            ctx.loop.run_until_complete(old_kernel.stop())
        except Exception as exc:  # noqa: BLE001 - old bricks may already be gone
            r.kernel_note(f"previous stack did not stop cleanly: {exc}")
    else:
        ctx.state.caps_applied = False

    # 4. Commit the swap.
    ctx.kernel = new_kernel
    ctx.resolved = target
    ctx.model = model_name(target)
    ctx.state.counts_shown = False

    if ctx.session_store is not None:
        try:
            ctx.session_store.update(stack=target.name, model=ctx.model)
        except OSError:
            pass

    # 5. Re-render the header so model / stack name / tool count show the new stack.
    r.session_start(
        stack=target,
        workspace=ctx.workspace,
        version=__version__,
        session_id=ctx.session_id or "-",
        model=ctx.model,
        mode=ctx.state.mode,
    )
    if getattr(ctx.kernel, "_started", False):
        try:
            listing = ctx.loop.run_until_complete(ctx.kernel.call("tools", "list", {}))
            r.tool_count(len(listing.get("tools", [])))
            ctx.state.counts_shown = True
        except ProtocolError:
            pass  # no tools brick bound — the next goal will settle the count


def cmd_workspace(ctx: "ReplContext", _args: str) -> None:
    console.print(str(ctx.workspace))


def cmd_mode(ctx: "ReplContext", _args: str) -> None:
    ctx.state.mode = next_mode(ctx.state.mode)
    if getattr(ctx.kernel, "_started", False):
        ctx.kernel.restrict_capabilities(withheld_capabilities(ctx.state.mode))
    ctx.renderer.info(f"mode: {ctx.state.mode}")
    if ctx.session_store is not None:
        ctx.session_store.update(mode=ctx.state.mode)


def cmd_model(ctx: "ReplContext", args: str) -> None:
    """List the models the bound inference brick reports, pick one, and switch to it live.

    Switching means rebinding the ``inference`` brick with a new ``model`` in its config and
    restarting just that brick through its supervisor. A failed switch leaves the previous model
    bound and working."""
    r = ctx.renderer
    if not _ensure_kernel(ctx):
        return
    try:
        listing = ctx.loop.run_until_complete(ctx.kernel.call("inference", "models", {}))
    except ProtocolError as exc:
        if exc.code in (METHOD_NOT_FOUND, UNSUPPORTED_METHOD):
            r.error("the bound inference brick does not implement models()")
        elif exc.code == CONTRACT_NOT_BOUND:
            r.error("no inference brick is bound")
        else:
            r.error(f"could not list models: {exc.message}")
        return

    models = listing.get("models", [])
    if not models:
        r.info("the inference brick reported no models")
        return

    wanted = args.strip()
    if wanted:
        if not any(m["id"] == wanted for m in models):
            r.error(f"no model {wanted!r} in the inference brick's catalogue")
            return
        chosen = wanted
    else:
        labels = [_model_label(m, current=ctx.model) for m in models]
        idx = select(labels, prompt="model", console=console, err_console=r.err)
        if idx is None:
            r.info("no change")
            return
        chosen = models[idx]["id"]

    if chosen == ctx.model:
        r.info(f"already using {chosen}")
        return

    previous = ctx.model
    try:
        ctx.loop.run_until_complete(ctx.kernel.rebind("inference", config={"model": chosen}))
    except Exception as exc:  # noqa: BLE001 - any failure at all must leave the old model working
        r.error(f"switch failed, still on {previous}: {exc}")
        return

    ctx.model = chosen
    r.info(f"model: {chosen}")
    if ctx.session_store is not None:
        ctx.session_store.update(model=chosen)


def _model_label(m: dict, *, current: str) -> str:
    mark = "* " if m.get("id") == current else "  "
    label = f"{mark}{m['id']}"
    extra = []
    if m.get("context_window"):
        extra.append(f"{m['context_window']:,} ctx")
    if m.get("description"):
        extra.append(str(m["description"]))
    return f"{label} — {' · '.join(extra)}" if extra else label


def cmd_context(ctx: "ReplContext", _args: str) -> None:
    """What is loaded this session, and the token usage the running UI already shows."""
    c = console
    show_stack(ctx.resolved)
    c.print(f"  [v.meta]workspace   [/] {ctx.workspace}")
    c.print(f"  [v.meta]mode        [/] {ctx.state.mode}  ({PLAN_MODE_NOTE if ctx.state.mode == 'plan' else 'stack grant in full'})")
    c.print(f"  [v.meta]model       [/] {ctx.model}")

    binds_conversation = ctx.resolved.binding_for("conversation") is not None
    sid = ctx.session_id or "(none)"
    hist = "loaded from local history" if binds_conversation else "not stored (no conversation brick)"
    c.print(f"  [v.meta]session     [/] {sid}  ({hist})")

    if ctx.mentions:
        c.print("  [v.meta]@ mentions  [/]")
        for m in ctx.mentions:
            note = f"{m.bytes} bytes" if m.kind == "file" else f"{m.bytes} entries" if m.kind == "dir" else "unresolved"
            if getattr(m, "truncated", False):
                note += ", truncated"
            c.print(f"    {m.kind:<7} {m.path}  ({note})")

    cell = usage_cell(getattr(ctx.renderer, "_usage", None))
    c.print(f"  [v.meta]tokens      [/] {cell or 'not reported yet'}")


def cmd_compact(ctx: "ReplContext", _args: str) -> None:
    """Ask the orchestrator to compress its working context. Additive contract method — degrade
    with a plain message if the bound orchestrator does not implement it."""
    r = ctx.renderer
    if not _ensure_kernel(ctx):
        return
    try:
        res = ctx.loop.run_until_complete(
            ctx.kernel.call("orchestrator", "compact", {"session_id": ctx.session_id or "default"})
        )
    except ProtocolError as exc:
        if exc.code in (METHOD_NOT_FOUND, UNSUPPORTED_METHOD, INVALID_PARAMS):
            r.info("the bound orchestrator does not support /compact")
        elif exc.code == CONTRACT_NOT_BOUND:
            r.error("no orchestrator is bound")
        else:
            r.error(f"compact failed: {exc.message}")
        return

    status = res.get("status")
    if status == "unsupported":
        r.info("the bound orchestrator does not support /compact")
    elif status == "noop":
        r.info("nothing to compact")
    elif status == "compacted":
        before = res.get("turns_before", "?")
        r.info(f"compacted {before} turn(s) into a summary")
        summary = (res.get("summary") or "").strip()
        if summary:
            r.info(summary)
    else:
        r.info(f"compact returned status {status!r}")


# -- registry -----------------------------------------------------------------


REGISTRY: list[Command] = [
    Command("/help", "show the commands", cmd_help, aliases=("/?",)),
    Command("/model", "list inference models and switch the bound one", cmd_model, usage="[id]"),
    Command("/context", "what is loaded now and the token usage in the footer", cmd_context),
    Command("/compact", "ask the orchestrator to compress its working context", cmd_compact),
    Command(
        "/stack",
        "the active stack; 'list' to pick another, or <name> to switch",
        cmd_stack,
        usage="[list|name]",
    ),
    Command("/workspace", "the workspace root", cmd_workspace),
    Command("/mode", "cycle the session mode (plan <-> auto)", cmd_mode),
    Command("/exit", "leave (Ctrl-D also works)", cmd_exit, aliases=("/quit",)),
]

_BY_TOKEN: dict[str, Command] = {tok: cmd for cmd in REGISTRY for tok in cmd.tokens}


def lookup(token: str) -> Command | None:
    return _BY_TOKEN.get(token)


def render_help() -> str:
    width = max(len(c.left) for c in REGISTRY)
    lines = ["commands:"]
    lines += [f"  {c.left:<{width}}  {c.summary}" for c in REGISTRY]
    lines += [
        "",
        "@<path> in a goal attaches that file or directory to the turn (no tab completion).",
        PLAN_MODE_NOTE,
        "Anything else is sent to the orchestrator as a goal.",
    ]
    return "\n".join(lines)


def initial_model(resolved: "ResolvedStack") -> str:
    return model_name(resolved)


__all__ = [
    "Command",
    "ReplContext",
    "REGISTRY",
    "_SessionState",
    "lookup",
    "render_help",
    "show_stack",
    "initial_model",
    "_wire_events",
    "_override_binding_config",
]
