"""The interactive CLI: the presentation layer and the bare entry point.

All hermetic — the Renderer is driven by synthetic orchestrator events, and the entry-point tests
quit before the kernel starts a brick.
"""

from __future__ import annotations

import io

from rich.console import Console
from typer.testing import CliRunner

from veridian.cli.main import app
from veridian.cli.ui import Renderer, model_markdown
from veridian.cli.ui.theme import _Glyphs

runner = CliRunner()


def _renderer() -> tuple[Renderer, io.StringIO]:
    buf = io.StringIO()
    con = Console(file=buf, width=80, force_terminal=False, color_system=None, theme=None)
    return Renderer(con, con), buf


# -- rendering --------------------------------------------------------------------


def test_delta_stream_separates_the_four_channels():
    r, buf = _renderer()
    r.run_begin("make the tests pass")
    r.delta("step", {"kind": "plan", "steps": ["read code", "edit", "run tests"]})
    r.delta("step", {"kind": "active", "description": "edit the file"})
    r.delta("tool", {"name": "write_file", "input": {"path": "a.py", "contents": "x = 1"}})
    r.delta("tool", {"name": "write_file", "output": "wrote 5 bytes", "is_error": False})
    r.delta("message", {"text": "Done. The **suite** is green now."})
    r.run_end({"status": "completed", "iterations": 3, "summary": "all tests pass"})
    out = buf.getvalue()

    assert "make the tests pass" in out          # user goal (rule)
    assert "read code" in out and "3. run tests" in out
    assert "edit the file" in out                # active step
    assert "write_file" in out and "a.py" in out  # tool call + compact input
    assert "wrote 5 bytes" in out                # tool result
    assert "suite" in out                        # model prose (markdown stripped of **)
    assert "completed" in out and "3 iteration(s)" in out


def test_tool_error_and_long_output_are_marked_and_clipped():
    r, buf = _renderer()
    r.delta("tool", {"name": "run_command", "output": "\n".join(f"line {i}" for i in range(40)), "is_error": True})
    out = buf.getvalue()
    assert "err" in out and "run_command" in out
    assert "line 0" in out
    assert "+28 more lines" in out               # 40 - 12 shown
    assert "line 39" not in out


def test_run_interrupted_is_reported_without_a_traceback():
    r, buf = _renderer()
    r.run_begin("something")
    r.run_interrupted()
    assert "interrupted" in buf.getvalue()


def test_error_goes_to_stderr_console():
    r, buf = _renderer()
    r.error("no brick bound to contract 'inference'")
    assert "error: no brick bound" in buf.getvalue()


def test_model_markdown_renders_a_code_fence():
    con = Console(file=io.StringIO(), width=80, force_terminal=False, color_system=None)
    con.print(model_markdown("Here:\n\n```python\ndef f():\n    return 1\n```\n"))
    out = con.file.getvalue()
    assert "def f():" in out
    assert "return 1" in out


def test_glyphs_fall_back_to_ascii_when_stdout_cannot_encode(monkeypatch):
    class _Cp1252:
        encoding = "cp1252"

    monkeypatch.setattr("veridian.cli.ui.theme.sys.stdout", _Cp1252())
    g = _Glyphs()
    assert g.caret == ">" and g.arrow == "->" and g.bullet == "-"


# -- the bare entry point -------------------------------------------------------


def test_bare_invocation_starts_a_session_and_exits_cleanly():
    r = runner.invoke(app, [], input="/exit\n")
    assert r.exit_code == 0
    assert "Veridian" in r.output
    assert "Type a goal" in r.output


def test_bare_invocation_exits_on_eof():
    r = runner.invoke(app, [], input="")
    assert r.exit_code == 0
    assert "bye" in r.output


def test_unknown_slash_command_is_reported_not_fatal():
    r = runner.invoke(app, [], input="/nope\n/exit\n")
    assert r.exit_code == 0
    assert "unknown command" in r.output


def test_version_flag_matches_version_command():
    flag = runner.invoke(app, ["--version"])
    cmd = runner.invoke(app, ["version"])
    assert flag.exit_code == 0 and cmd.exit_code == 0
    assert flag.output.strip() == cmd.output.strip()


def test_interactive_ctrl_c_unwinds_one_turn_and_keeps_the_session(monkeypatch):
    """A KeyboardInterrupt out of a running turn must return to the prompt with the kernel up."""
    import asyncio

    from veridian.cli import interactive

    events_seen: list[str] = []

    class _FakeKernel:
        _started = True

        async def call_stream(self, *_a, **_k):
            await asyncio.sleep(30)  # a real run in progress when the signal lands

        async def stop(self):
            events_seen.append("stopped")

    class _FakeRenderer:
        def __init__(self):
            self._prompts = iter(["do a thing", "/exit"])

        def read_prompt(self):
            return next(self._prompts)

        def run_begin(self, goal):
            events_seen.append(f"begin:{goal}")

        def run_interrupted(self):
            events_seen.append("interrupted")

        def clear_activity(self):
            pass

        def newline(self):
            pass

        def info(self, *_):
            pass

        def error(self, *_):
            events_seen.append("error")

    loop = asyncio.new_event_loop()
    real_ruc = loop.run_until_complete
    state = {"fired": False}

    def _ruc(future):
        # First call is the turn task: let it actually begin, then model the signal
        # arriving from the run loop while the run is still in progress.
        if not state["fired"]:
            state["fired"] = True
            try:
                real_ruc(asyncio.wait_for(asyncio.shield(future), 0.2))
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                pass
            raise KeyboardInterrupt
        return real_ruc(future)

    monkeypatch.setattr(loop, "run_until_complete", _ruc)
    try:
        interactive._repl(loop, _FakeKernel(), object(), "/ws", 4, _FakeRenderer())
    finally:
        real_ruc(loop.shutdown_asyncgens())
        loop.close()

    assert events_seen == ["begin:do a thing", "interrupted"]  # ran the turn, caught, then /exit


def test_run_translates_ctrl_c_into_a_clean_interrupt(monkeypatch):
    def _boom(coro=None, *_a, **_k):
        if coro is not None:
            coro.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("veridian.cli.commands.run_cmds.asyncio.run", _boom)
    r = runner.invoke(app, ["run", "do a thing", "--stack", "stacks/default.toml"])
    assert r.exit_code == 130
    assert "interrupted" in r.output


def test_stack_without_orchestrator_is_rejected_for_interactive(tmp_path):
    s = tmp_path / "s.toml"
    s.write_text('[stack]\nname="x"\n[bindings]\ncontext = "bricks/context/default"\n', encoding="utf-8")
    r = runner.invoke(app, ["--stack", str(s)], input="/exit\n")
    assert r.exit_code == 1
    assert "orchestrator" in r.output
