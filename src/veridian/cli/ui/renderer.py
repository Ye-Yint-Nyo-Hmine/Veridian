"""Turn orchestrator events into terminal output.

This is the whole presentation layer for the interactive session. It is driven only by
``orchestrator.delta`` events and the protocol's run result — never by a provider-specific response
shape — so swapping the inference or orchestrator brick changes nothing here.

Layout contract, so the four streams stay visually distinct:

* user input   — a bold ``›`` caret, and the goal echoed under a dim rule
* model output — flush left under a ``◈`` marker, rendered as Markdown
* tool calls   — indented, ``→`` for a call and ``ok``/``err`` for the result, ``∟`` for a
                 nested / continuation line
* status       — dim: the transient activity line, the ``↠`` completion line, and the
                 model / context-usage / branch footer above the prompt

SEAM: the boot design also shows a "reasoning enabled" indicator and "skills" / "MCP" counts.
Veridian v0 has no reasoning-toggle, no skills subsystem, and no MCP subsystem, so those fields
are deliberately not rendered — no invented values. When such a concept lands, the boot header in
:meth:`Renderer.session_start` and the counts line in :meth:`Renderer.tool_count` are where it
belongs.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.text import Text

from veridian.cli.ui.activity import Activity
from veridian.cli.ui.markdown import model_markdown
from veridian.cli.ui.theme import GLYPHS, THEME, logo

_TOOL_INPUT_MAX = 140
_TOOL_OUTPUT_LINES = 12


class Renderer:
    def __init__(self, console: Console, err_console: Console | None = None) -> None:
        self.console = console
        self.err = err_console or Console(stderr=True, theme=THEME)
        self._activity = Activity(console)
        self._last_message = ""
        self._responded = False
        self._run_started: float | None = None
        self._usage: dict[str, Any] | None = None

    # -- session framing ---------------------------------------------------------

    def session_start(
        self,
        *,
        stack: Any,
        workspace: Path,
        version: str,
        session_id: str,
        model: str,
        mode: str,
    ) -> None:
        c = self.console
        c.print()
        for line in logo():
            c.print(Text(line, style="v.logo"))
        c.print()
        c.print(Text(f"Veridian v-{version}", style="v.user"))
        # SEAM: "<model> | reasoning · <stack>" in the design — reasoning has no backing concept.
        c.print(Text(f"{model} {GLYPHS.sep} {stack.name}", style="v.meta"))
        c.print(Text(_tilde(workspace), style="v.meta"))
        c.print(Text(f"Session - {session_id}", style="v.meta"))
        c.print()
        c.print(
            Text(
                f"Type a goal and press Enter. /help for commands {GLYPHS.sep} /mode to cycle "
                f"mode {GLYPHS.sep} Ctrl-C interrupts a run {GLYPHS.sep} Ctrl-D exits.",
                style="v.meta",
            )
        )

    def tool_count(self, n: int) -> None:
        """Printed once, after the kernel is up and ``tools.list`` is answerable.

        SEAM: the design's "X tools | X skills | X MCPs" — only the tool count is real."""
        self._activity.clear()
        self.console.print(Text(f"{n} tools", style="v.meta"))

    def footer(self, *, model: str, branch: str | None, mode: str) -> None:
        """The status line + mode line, reprinted just above each prompt. A plain reprint — no
        Live region, no animation."""
        self._activity.clear()
        parts = [model or "?"]
        cell = usage_cell(self._usage)
        if cell:
            parts.append(cell)
        if branch:
            parts.append(branch)
        self.console.print(Text("  " + "  |  ".join(parts), style="v.meta"))
        mode_line = f"  {GLYPHS.mode} {mode} mode (/mode to cycle)"
        if mode == "plan":
            # Say what plan mode really enforces — sandbox is genuine, the write withholding is not.
            mode_line += f" {GLYPHS.sep} sandbox enforced, write advisory"
        self.console.print(Text(mode_line, style="v.meta"))

    def session_end(self) -> None:
        self._activity.clear()
        self.console.print(Text("bye", style="v.meta"))

    def newline(self) -> None:
        self._activity.clear()
        self.console.print()

    # -- prompt ---------------------------------------------------------------------

    def read_prompt(self) -> str:
        """Block for one line of input. Raises EOFError on Ctrl-D, KeyboardInterrupt on Ctrl-C."""
        self._activity.clear()
        self.console.print()
        self.console.print(Text(GLYPHS.caret, style="v.prompt"), end=" ")
        return input()

    # -- one run ------------------------------------------------------------------

    def run_begin(self, goal: str) -> None:
        self._activity.clear()
        self._last_message = ""
        self._responded = False
        self._run_started = time.monotonic()
        self.console.rule(Text(goal, style="v.user"), style="v.rule", align="left")
        self._activity.show("thinking")

    def delta(self, event: str, data: dict[str, Any]) -> None:
        if event == "usage":
            # not printed — folded into the footer above the next prompt
            self._usage = dict(data)
            return
        if event == "message":
            self._last_message = (data.get("text") or "").strip()
        renderable, next_label = self._render(event, data)
        if renderable is not None:
            self._activity.clear()
            self.console.print(renderable)
        if next_label is not None:
            self._activity.show(next_label)

    def run_end(self, result: dict[str, Any]) -> None:
        self._activity.clear()
        status = result.get("status", "?")
        style = {"completed": "v.ok", "failed": "v.err", "max_iterations": "v.warn"}.get(status, "v.meta")
        self.console.print()
        self.console.print(
            Text.assemble(
                (f"{GLYPHS.done} ", style),
                (f"Generated in {_elapsed(self._run_started)} ", "v.meta"),
                (f"{GLYPHS.sep} ", "v.meta"),
                (status, style),
                (f" {GLYPHS.sep} {datetime.now().strftime('%I:%M %p').lstrip('0')}", "v.meta"),
            )
        )
        summary = (result.get("summary") or "").strip()
        # The orchestrator's summary is usually the last message verbatim — don't print it twice.
        if summary and summary != status and summary != self._last_message:
            self.console.print(model_markdown(summary))

    def run_interrupted(self) -> None:
        self._activity.clear()
        self.console.print()
        self.console.print(Text("interrupted — the brick may still be finishing in the background", style="v.warn"))

    # -- out-of-band --------------------------------------------------------------

    def error(self, message: str) -> None:
        self._activity.clear()
        self.err.print(Text(f"error: {message}", style="v.err"))

    def kernel_note(self, text: str) -> None:
        self._activity.clear()
        self.console.print(Text(f"{GLYPHS.bullet} {text}", style="v.meta"))

    def info(self, text: str) -> None:
        self._activity.clear()
        self.console.print(Text(text, style="v.meta"))

    def clear_activity(self) -> None:
        self._activity.clear()

    # -- event -> renderable ----------------------------------------------------------

    def _render(self, event: str, data: dict[str, Any]) -> tuple[RenderableType | None, str | None]:
        if event == "log":
            msg = data.get("message", "")
            return (Text(f"{GLYPHS.bullet} {msg}", style="v.meta") if msg else None), "thinking"

        if event == "step":
            if data.get("kind") == "plan":
                steps = data.get("steps", [])
                lines = [Text("plan", style="v.plan")]
                lines += [Text(f"  {i}. {s}", style="v.meta") for i, s in enumerate(steps, 1)]
                return Group(*lines), "thinking"
            if data.get("kind") == "active":
                return Text(f"step {GLYPHS.sep} {data.get('description', '')}", style="v.step"), "thinking"
            return None, "thinking"

        if event == "tool":
            name = data.get("name", "?")
            if "output" in data:
                is_err = bool(data.get("is_error"))
                head = Text.assemble(
                    (f"  {GLYPHS.nest} ", "v.meta"),
                    ("err ", "v.tool.err") if is_err else ("ok  ", "v.tool.ok"),
                    (name, "v.tool"),
                )
                body = _tool_output(str(data.get("output", "")))
                return Group(head, *body), "thinking"
            shown = _compact(data.get("input"))
            return (
                Text.assemble((f"  {GLYPHS.arrow} ", "v.tool"), (name, "v.tool"), (f"  {shown}", "v.meta")),
                f"running {name}",
            )

        if event == "message":
            text = data.get("text", "")
            if not text:
                return None, "thinking"
            md = model_markdown(text)
            if not self._responded:
                self._responded = True
                return Group(Text(GLYPHS.response, style="v.step"), md), "thinking"
            return md, "thinking"

        return None, "thinking"


def _tilde(path: Path) -> str:
    p = str(path)
    try:
        home = str(Path.home())
        if p == home or p.startswith(home + "\\") or p.startswith(home + "/"):
            return "~" + p[len(home):]
    except (OSError, RuntimeError):
        pass
    return p


def _elapsed(started: float | None) -> str:
    if started is None:
        return "0s"
    sec = max(0.0, time.monotonic() - started)
    return f"{sec * 1000:.0f}ms" if sec < 1 else f"{sec:.0f}s"


def _k(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def usage_cell(usage: dict[str, Any] | None) -> str | None:
    """``<used>k/<window>k`` when a provider reported usage; ``None`` (absent, not zero) when it
    did not."""
    if not usage:
        return None
    num = usage.get("context_tokens")
    if num is None:
        num = usage.get("input_tokens")
    if num is None:
        return None
    win = usage.get("context_window")
    return f"{_k(int(num))}/{_k(int(win))}" if win else _k(int(num))


def _compact(value: Any) -> str:
    try:
        s = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        s = str(value)
    s = " ".join(s.split())
    return s if len(s) <= _TOOL_INPUT_MAX else s[: _TOOL_INPUT_MAX - 1] + "…"


def _tool_output(raw: str) -> list[RenderableType]:
    raw = raw.rstrip("\n")
    if not raw:
        return []
    lines = raw.split("\n")
    clipped = lines[:_TOOL_OUTPUT_LINES]
    out: list[RenderableType] = [Text(f"    {ln}", style="v.meta") for ln in clipped]
    if len(lines) > _TOOL_OUTPUT_LINES:
        out.append(Text(f"    … (+{len(lines) - _TOOL_OUTPUT_LINES} more lines)", style="v.meta"))
    return out
