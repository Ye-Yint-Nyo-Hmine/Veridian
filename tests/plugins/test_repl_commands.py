"""The interactive slash-command registry (`veridian.cli.repl_commands`)."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from rich.console import Console

from veridian.cli import repl_commands
from veridian.cli.repl_commands import REGISTRY, ReplContext, _SessionState, lookup, render_help
from veridian.cli.ui import Renderer
from veridian.contracts.errors import METHOD_NOT_FOUND, ProtocolError


class _Binding:
    def __init__(self, contract: str, name: str) -> None:
        self.contract = contract
        self.manifest = type("M", (), {"name": name})()


class _Policy:
    grant = {"contract:inference"}


class _Stack:
    name = "fake"
    description = ""
    policy = _Policy()

    def active(self):
        return [_Binding("inference", "inference/local")]

    def binding_for(self, contract):
        return _Binding(contract, "x") if contract == "conversation" else None


def _ctx(kernel, *, model: str = "m0"):
    buf = io.StringIO()
    con = Console(file=buf, width=100, force_terminal=False, color_system=None)
    renderer = Renderer(con, con)
    ctx = ReplContext(
        loop=asyncio.new_event_loop(),
        kernel=kernel,
        resolved=_Stack(),
        workspace=Path("."),
        renderer=renderer,
        state=_SessionState("plan"),
        max_iterations=4,
        model=model,
        session_id="sid",
    )
    return ctx, buf


# -- registry -----------------------------------------------------------------


def test_lookup_by_name_and_alias():
    assert lookup("/help").name == "/help"
    assert lookup("/?").name == "/help"
    assert lookup("/quit").name == "/exit"
    assert lookup("/nope") is None


def test_render_help_lists_every_registered_token_and_the_fallthrough():
    text = render_help()
    for cmd in REGISTRY:
        for tok in cmd.tokens:
            assert tok in text, tok
    assert "Anything else is sent to the orchestrator as a goal." in text
    assert "contract:sandbox is enforced" in text  # the honest plan-mode note


# -- /mode -------------------------------------------------------------------


def test_mode_command_cycles_and_restricts():
    class _K:
        _started = True

        def __init__(self):
            self.calls = []

        def restrict_capabilities(self, withheld):
            self.calls.append(set(withheld))

    k = _K()
    ctx, buf = _ctx(k)
    lookup("/mode").handler(ctx, "")
    assert ctx.state.mode == "auto" and k.calls == [set()]
    lookup("/mode").handler(ctx, "")
    assert ctx.state.mode == "plan" and k.calls[-1] == {"workspace:write", "contract:sandbox"}
    assert "mode: auto" in buf.getvalue() and "mode: plan" in buf.getvalue()


# -- /model ----------------------------------------------------------------------


def test_model_reports_when_inference_has_no_models_method():
    class _K:
        async def call(self, contract, method, params):
            raise ProtocolError(METHOD_NOT_FOUND, "no such method")

    ctx, buf = _ctx(_K())
    lookup("/model").handler(ctx, "")
    assert "does not implement models()" in buf.getvalue()


def test_model_switch_rebinds_and_updates_the_footer_model(monkeypatch):
    class _K:
        def __init__(self):
            self.rebound = None

        async def call(self, contract, method, params):
            assert (contract, method) == ("inference", "models")
            return {"models": [{"id": "m0"}, {"id": "m1", "context_window": 8192}]}

        async def rebind(self, contract, *, config):
            self.rebound = (contract, config)

    k = _K()
    ctx, buf = _ctx(k, model="m0")
    monkeypatch.setattr(repl_commands, "select", lambda *a, **kw: 1)
    lookup("/model").handler(ctx, "")
    assert k.rebound == ("inference", {"model": "m1"})
    assert ctx.model == "m1" and "model: m1" in buf.getvalue()


def test_model_failed_switch_keeps_the_previous_model(monkeypatch):
    class _K:
        async def call(self, contract, method, params):
            return {"models": [{"id": "m0"}, {"id": "m1"}]}

        async def rebind(self, contract, *, config):
            raise RuntimeError("brick would not come up")

    ctx, buf = _ctx(_K(), model="m0")
    monkeypatch.setattr(repl_commands, "select", lambda *a, **kw: 1)
    lookup("/model").handler(ctx, "")
    assert ctx.model == "m0"
    assert "switch failed, still on m0" in buf.getvalue()


def test_model_explicit_id_skips_the_selector(monkeypatch):
    class _K:
        def __init__(self):
            self.rebound = None

        async def call(self, contract, method, params):
            return {"models": [{"id": "m0"}, {"id": "m1"}]}

        async def rebind(self, contract, *, config):
            self.rebound = config

    def _boom(*a, **kw):  # pragma: no cover - must not be reached
        raise AssertionError("selector should not be shown for an explicit id")

    monkeypatch.setattr(repl_commands, "select", _boom)
    k = _K()
    ctx, _ = _ctx(k, model="m0")
    lookup("/model").handler(ctx, "m1")
    assert k.rebound == {"model": "m1"}


# -- /compact ----------------------------------------------------------------------


def test_compact_degrades_when_the_orchestrator_lacks_the_method():
    class _K:
        async def call(self, contract, method, params):
            raise ProtocolError(METHOD_NOT_FOUND, "unknown method compact")

    ctx, buf = _ctx(_K())
    lookup("/compact").handler(ctx, "")
    assert "does not support /compact" in buf.getvalue()


def test_compact_reports_a_compacted_result():
    class _K:
        async def call(self, contract, method, params):
            return {"status": "compacted", "summary": "we did X and Y", "turns_before": 6, "turns_after": 1}

    ctx, buf = _ctx(_K())
    lookup("/compact").handler(ctx, "")
    out = buf.getvalue()
    assert "compacted 6 turn" in out and "we did X and Y" in out


def test_compact_noop_is_plain():
    class _K:
        async def call(self, contract, method, params):
            return {"status": "noop", "turns_before": 1, "turns_after": 1}

    ctx, buf = _ctx(_K())
    lookup("/compact").handler(ctx, "")
    assert "nothing to compact" in buf.getvalue()


# -- /context ------------------------------------------------------------------


def test_context_shows_the_loaded_session_state(capsys):
    # /context, like /help and /stack, prints to the shared console (stdout), not the renderer.
    class _K:
        pass

    ctx, _ = _ctx(_K(), model="qwen3.5:4b")
    ctx.session_id = "abcdef"
    lookup("/context").handler(ctx, "")
    out = capsys.readouterr().out
    assert "qwen3.5:4b" in out and "abcdef" in out and "plan" in out
    assert "not reported yet" in out  # no usage delta seen yet


# -- /stack ------------------------------------------------------------------------

import dataclasses as _dc

from veridian.cli.commands.stack_cmds import DiscoveredStack


@_dc.dataclass
class _FakeBinding:
    contract: str
    config: dict


class _FakeTarget:
    """Enough of a ResolvedStack for the switch path: name, path, bindings, binding_for."""

    def __init__(self, name, path, *, inference=True, orchestrator=True):
        self.name = name
        self.path = Path(path)
        self.description = ""
        self.policy = _Policy()
        self.bindings = [_FakeBinding("orchestrator", {})]
        if inference:
            self.bindings.append(_FakeBinding("inference", {"model": "m-new"}))
        self._inference = inference
        self._orchestrator = orchestrator

    def active(self):
        return []

    def bound_map(self):
        return [b.contract for b in self.bindings]

    def binding_for(self, contract):
        if contract == "inference" and not self._inference:
            return None
        if contract == "orchestrator" and not self._orchestrator:
            return None
        return next((b for b in self.bindings if b.contract == contract), None)


class _FakeSwitchKernel:
    instances: list = []

    def __init__(self, stack=None, *, workspace_root=None, fail_start=False):
        self.stack = stack
        self.workspace_root = workspace_root
        self._started = False
        self._fail_start = fail_start
        self.stopped = False
        self.restricted = None
        self.events = type("E", (), {"subscribe": lambda self, *a: None})()
        _FakeSwitchKernel.instances.append(self)

    async def start(self):
        if self._fail_start:
            raise RuntimeError("brick would not come up")
        self._started = True

    async def stop(self):
        self.stopped = True
        self._started = False

    def restrict_capabilities(self, withheld):
        self.restricted = set(withheld)

    async def call(self, contract, method, params, **kw):
        return {"tools": [{"name": "a"}, {"name": "b"}, {"name": "c"}]}


def _started_kernel():
    k = _FakeSwitchKernel()
    k._started = True
    return k


def test_stack_bare_with_a_running_kernel_still_shows_the_active_stack(capsys):
    class _K:
        _started = True

    ctx, _ = _ctx(_K())
    lookup("/stack").handler(ctx, "")
    out = capsys.readouterr().out
    assert "fake" in out and "inference/local" in out  # show_stack output, unregressed


def test_stack_bare_without_a_kernel_lists_through_the_selector(monkeypatch):
    class _K:
        _started = False

    ctx, buf = _ctx(_K())
    monkeypatch.setattr(
        repl_commands,
        "discover_stacks",
        lambda: [DiscoveredStack(Path("/s/x.toml"), "x", ("inference",), "built-in")],
    )
    seen = {}

    def _sel(options, **kw):
        seen["options"] = list(options)
        return None

    monkeypatch.setattr(repl_commands, "select", _sel)
    lookup("/stack").handler(ctx, "")
    assert len(seen["options"]) == 1 and "no change" in buf.getvalue()


def test_stack_list_marks_the_active_stack_and_offers_the_numbered_selector(monkeypatch):
    _FakeSwitchKernel.instances.clear()
    ctx, buf = _ctx(_FakeSwitchKernel())
    ctx.resolved = _FakeTarget("default", "/s/default.toml")

    entries = [
        DiscoveredStack(Path("/s/default.toml"), "default", ("inference", "orchestrator"), "built-in"),
        DiscoveredStack(Path("/s/research.toml"), "research", ("inference", "context"), "built-in"),
    ]
    monkeypatch.setattr(repl_commands, "discover_stacks", lambda: entries)
    seen = {}

    def _sel(options, **kw):
        seen["options"] = list(options)
        return None

    monkeypatch.setattr(repl_commands, "select", _sel)
    lookup("/stack").handler(ctx, "list")

    assert seen["options"][0].startswith("* default")
    assert seen["options"][1].startswith("  research")
    assert "no change" in buf.getvalue()


def test_stack_switch_preserves_the_session_and_swaps_the_kernel(monkeypatch):
    _FakeSwitchKernel.instances.clear()
    old = _started_kernel()
    ctx, buf = _ctx(old, model="m-old")
    ctx.session_id = "keep-me"

    updates: dict = {}

    class _Store:
        def update(self, **kw):
            updates.update(kw)

    ctx.session_store = _Store()

    target = _FakeTarget("research", "/x/research.toml")
    monkeypatch.setattr(repl_commands, "resolve_stack_ref", lambda ref: Path("/x/research.toml"))
    monkeypatch.setattr(repl_commands, "load_stack", lambda p: target)
    monkeypatch.setattr(repl_commands, "model_name", lambda s: "m-new")
    monkeypatch.setattr(repl_commands, "Kernel", _FakeSwitchKernel)

    lookup("/stack").handler(ctx, "research")

    assert ctx.session_id == "keep-me"          # same id -> same conversation/history
    assert ctx.resolved is target
    assert ctx.model == "m-new"
    assert old.stopped is True
    new = ctx.kernel
    assert isinstance(new, _FakeSwitchKernel) and new is not old and new._started is True
    assert new.restricted == {"workspace:write", "contract:sandbox"}  # plan mode reapplied
    orch = next(b for b in ctx.resolved.bindings if b.contract == "orchestrator")
    assert orch.config["session_id"] == "keep-me"  # carried into the new orchestrator binding
    assert updates == {"stack": "research", "model": "m-new"}
    assert ctx.state.caps_applied is True


def test_stack_switch_failure_leaves_the_old_kernel_serving(monkeypatch):
    _FakeSwitchKernel.instances.clear()
    old = _started_kernel()
    ctx, buf = _ctx(old, model="m-old")
    ctx.session_id = "keep-me"
    original = ctx.resolved

    target = _FakeTarget("research", "/x/research.toml")
    monkeypatch.setattr(repl_commands, "resolve_stack_ref", lambda ref: Path("/x/research.toml"))
    monkeypatch.setattr(repl_commands, "load_stack", lambda p: target)
    monkeypatch.setattr(repl_commands, "model_name", lambda s: "m-new")
    monkeypatch.setattr(
        repl_commands, "Kernel", lambda *a, **kw: _FakeSwitchKernel(*a, fail_start=True, **kw)
    )

    lookup("/stack").handler(ctx, "research")

    assert ctx.kernel is old and old._started is True and old.stopped is False
    assert ctx.resolved is original and ctx.model == "m-old"
    assert "failed to start" in buf.getvalue()
    assert _FakeSwitchKernel.instances[-1].stopped is True  # the half-built kernel rolled back


def test_stack_switch_that_does_not_resolve_leaves_the_session_untouched(monkeypatch):
    from veridian.kernel.errors import StackConfigError

    _FakeSwitchKernel.instances.clear()
    old = _started_kernel()
    ctx, buf = _ctx(old, model="m-old")
    original = ctx.resolved

    def _boom(ref):
        raise StackConfigError("no stack named 'nope'")

    monkeypatch.setattr(repl_commands, "resolve_stack_ref", _boom)
    monkeypatch.setattr(repl_commands, "Kernel", _FakeSwitchKernel)

    lookup("/stack").handler(ctx, "nope")

    assert ctx.kernel is old and ctx.resolved is original and old.stopped is False
    assert _FakeSwitchKernel.instances == [old]  # no new kernel was built
    assert "did not load" in buf.getvalue()


def test_stack_switch_refuses_a_stack_with_no_inference_binding(monkeypatch):
    _FakeSwitchKernel.instances.clear()
    old = _started_kernel()
    ctx, buf = _ctx(old, model="m-old")
    original = ctx.resolved

    target = _FakeTarget("autonomous", "/x/autonomous.toml", inference=False)
    monkeypatch.setattr(repl_commands, "resolve_stack_ref", lambda ref: Path("/x/autonomous.toml"))
    monkeypatch.setattr(repl_commands, "load_stack", lambda p: target)
    monkeypatch.setattr(repl_commands, "Kernel", _FakeSwitchKernel)

    lookup("/stack").handler(ctx, "autonomous")

    out = buf.getvalue()
    assert "no inference binding" in out
    assert ctx.kernel is old and ctx.resolved is original and old.stopped is False
    assert _FakeSwitchKernel.instances == [old]  # refused before building a new kernel
