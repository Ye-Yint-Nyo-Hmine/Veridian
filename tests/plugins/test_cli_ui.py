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
from veridian.cli.ui.theme import GLYPHS, _Glyphs

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
    assert "Generated in" in out and "completed" in out  # the completion line


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
    assert g.response == "*" and g.nest == "\\_" and g.done == "=>" and g.mode == ">>"


# -- boot screen ----------------------------------------------------------------


class _Stack:
    name = "local-ollama"


def test_boot_screen_shows_real_identity_and_omits_unbacked_fields():
    from pathlib import Path

    r, buf = _renderer()
    r.session_start(
        stack=_Stack(), workspace=Path("/proj/veridian"), version="0.1.0",
        session_id="abcd1234", model="qwen3.5:4b", mode="plan",
    )
    out = buf.getvalue()
    assert "Veridian v-0.1.0" in out
    assert "qwen3.5:4b" in out and "local-ollama" in out
    assert "Session - abcd1234" in out
    assert "Type a goal" in out and "/mode" in out
    # no invented values for concepts Veridian v0 lacks
    assert "reasoning" not in out.lower()
    assert "skill" not in out.lower() and "mcp" not in out.lower()


def test_boot_screen_logo_is_ascii_when_stdout_cannot_encode(monkeypatch):
    from pathlib import Path

    class _Cp1252:
        encoding = "cp1252"

    monkeypatch.setattr("veridian.cli.ui.theme.sys.stdout", _Cp1252())
    r, buf = _renderer()
    r.session_start(
        stack=_Stack(), workspace=Path("/p"), version="0.1.0",
        session_id="x", model="m", mode="plan",
    )
    assert "V E R I D I A N" in buf.getvalue()


def test_tool_count_prints_only_the_real_count():
    r, buf = _renderer()
    r.tool_count(7)
    out = buf.getvalue()
    assert "7 tools" in out
    assert "skill" not in out.lower() and "mcp" not in out.lower()


# -- running layout -----------------------------------------------------------------


def test_response_marker_precedes_the_first_model_message_only():
    r, buf = _renderer()
    r.run_begin("g")
    r.delta("message", {"text": "first"})
    r.delta("tool", {"name": "read_file", "input": {"path": "a"}})
    r.delta("message", {"text": "second"})
    out = buf.getvalue()
    assert out.count(GLYPHS.response) == 1
    assert GLYPHS.nest not in out  # only a call so far, no result line


def test_nested_tool_result_uses_the_continuation_glyph():
    r, buf = _renderer()
    r.delta("tool", {"name": "grep", "output": "3 matches", "is_error": False})
    assert GLYPHS.nest in buf.getvalue()


def test_completion_line_carries_elapsed_and_clock_time():
    import re

    r, buf = _renderer()
    r.run_begin("g")
    r.run_end({"status": "completed", "iterations": 2, "summary": "ok"})
    out = buf.getvalue()
    assert GLYPHS.done in out and "Generated in" in out and "completed" in out
    assert re.search(r"\d{1,2}:\d{2}\s?[AP]M", out)


# -- footer (status + mode line) --------------------------------------------------


def test_footer_omits_usage_until_a_provider_reports_it():
    r, buf = _renderer()
    r.footer(model="qwen", branch="main", mode="plan")
    out = buf.getvalue()
    assert "qwen" in out and "main" in out
    assert "k/" not in out and "/1000k" not in out          # absent, not zero
    assert "plan mode (/mode to cycle)" in out
    # the honest guarantee: sandbox is enforced in plan mode, the write withholding is not
    assert "sandbox enforced, write advisory" in out


def test_footer_shows_context_usage_once_reported():
    r, buf = _renderer()
    r.delta("usage", {"input_tokens": 12000, "output_tokens": 400, "context_tokens": 12000, "context_window": 1000000})
    r.footer(model="qwen", branch=None, mode="auto")
    out = buf.getvalue()
    assert "12k/1000k" in out
    assert "auto mode" in out
    assert "advisory" not in out  # only plan mode carries the caveat


# -- git branch ----------------------------------------------------------------------


def test_current_branch_reads_ref_detached_and_worktree(tmp_path):
    from veridian.cli.ui.gitinfo import current_branch

    assert current_branch(tmp_path) is None  # not a checkout

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n", encoding="utf-8")
    assert current_branch(tmp_path) == "x"

    (tmp_path / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n", encoding="utf-8")
    assert current_branch(tmp_path) == "0123456"

    wt = tmp_path / "wt"
    wt.mkdir()
    real = tmp_path / ".git" / "worktrees" / "wt"
    real.mkdir(parents=True)
    (real / "HEAD").write_text("ref: refs/heads/wtbranch\n", encoding="utf-8")
    (wt / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
    assert current_branch(wt) == "wtbranch"


# -- mode ---------------------------------------------------------------------------


def test_mode_helpers():
    from veridian.cli.mode import next_mode, withheld_capabilities

    assert next_mode("plan") == "auto"
    assert next_mode("auto") == "plan"
    assert withheld_capabilities("plan") == frozenset({"workspace:write", "contract:sandbox"})
    assert withheld_capabilities("auto") == frozenset()


def test_mode_command_cycles_and_restricts_kernel_capabilities():
    from veridian.cli import interactive

    calls: list = []
    infos: list[str] = []

    class _K:
        _started = True

        def restrict_capabilities(self, withheld):
            calls.append(set(withheld))

    class _R:
        def __init__(self):
            self._p = iter(["/mode", "/mode", "/exit"])

        def read_prompt(self):
            return next(self._p)

        def info(self, text):
            infos.append(text)

        def newline(self):
            pass

    import asyncio

    loop = asyncio.new_event_loop()
    try:
        interactive._repl(loop, _K(), object(), "/ws", 4, _R())
    finally:
        loop.close()

    assert infos == ["mode: auto", "mode: plan"]
    assert calls == [set(), {"workspace:write", "contract:sandbox"}]


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

        async def call(self, *_a, **_k):
            return {"tools": []}

        def restrict_capabilities(self, *_a, **_k):
            pass

        async def stop(self):
            events_seen.append("stopped")

    class _FakeRenderer:
        def __init__(self):
            self._prompts = iter(["do a thing", "/exit"])

        def read_prompt(self):
            return next(self._prompts)

        def tool_count(self, *_):
            pass

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


def test_interactive_ctrl_c_sends_cancel_to_the_live_stream(monkeypatch):
    """A4 SEAM: when a run is streaming and Ctrl-C lands, the REPL cancels the live stream so the
    orchestrator (and, via the kernel, its downstream calls) stop rather than run on detached."""
    import asyncio

    from veridian.cli import interactive

    cancelled = {"count": 0}

    class _FakeStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(30)  # a run in progress: blocked producing the next delta

        async def result(self):
            await asyncio.sleep(30)

        async def cancel(self):
            cancelled["count"] += 1

    class _FakeKernel:
        _started = True

        async def call_stream(self, *_a, **_k):
            return _FakeStream()

        async def call(self, *_a, **_k):
            return {"tools": []}

        def restrict_capabilities(self, *_a, **_k):
            pass

        async def stop(self):
            pass

    class _FakeRenderer:
        def __init__(self):
            self._prompts = iter(["do a thing", "/exit"])

        def read_prompt(self):
            return next(self._prompts)

        def tool_count(self, *_):
            pass

        def run_begin(self, goal):
            pass

        def run_interrupted(self):
            pass

        def clear_activity(self):
            pass

        def newline(self):
            pass

        def info(self, *_):
            pass

        def error(self, *_):
            pass

    loop = asyncio.new_event_loop()
    real_ruc = loop.run_until_complete
    state = {"fired": False}

    def _ruc(future):
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

    assert cancelled["count"] == 1


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
