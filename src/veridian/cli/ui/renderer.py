"""Turn orchestrator events into terminal output.

This is the whole presentation layer for the interactive session. It is driven only by
``orchestrator.delta`` events and the protocol's run result — never by a provider-specific response
shape — so swapping the inference or orchestrator brick changes nothing here.

Layout contract, so the four streams stay visually distinct:

* user input   — a bold ``›`` caret, and the goal echoed under a dim rule
* model output — flush left, rendered as Markdown
* tool calls   — indented, ``→`` for a call and ``ok``/``err`` for the result
* status       — dim, and either transient (the activity line) or a single summary line
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.text import Text

from veridian.cli.ui.activity import Activity
from veridian.cli.ui.markdown import model_markdown
from veridian.cli.ui.theme import GLYPHS, THEME

_TOOL_INPUT_MAX = 140
_TOOL_OUTPUT_LINES = 12


class Renderer:
    def __init__(self, console: Console, err_console: Console | None = None) -> None:
        self.console = console
        self.err = err_console or Console(stderr=True, theme=THEME)
        self._activity = Activity(console)
        self._last_message = ""

    # -- session framing ---------------------------------------------------------

    def session_start(self, *, stack: Any, workspace: Path, version: str) -> None:
        c = self.console
        c.print(Text(f"Veridian {version}", style="v.user"))
        bindings = ", ".join(f"{b.contract}={b.manifest.name.split('/')[-1]}" for b in stack.active())
        c.print(Text(f"stack      {stack.name}", style="v.meta"))
        c.print(Text(f"inference  {_binding_name(stack, 'inference')}", style="v.meta"))
        c.print(Text(f"workspace  {workspace}", style="v.meta"))
        c.print(Text(f"bindings   {bindings}", style="v.meta"))
        c.print()
        c.print(
            Text(
                f"Type a goal and press Enter. /help for commands {GLYPHS.sep} Ctrl-C interrupts "
                f"a run {GLYPHS.sep} Ctrl-D exits.",
                style="v.meta",
            )
        )

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
        self.console.rule(Text(goal, style="v.user"), style="v.rule", align="left")
        self._activity.show("thinking")

    def delta(self, event: str, data: dict[str, Any]) -> None:
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
        iterations = result.get("iterations", 0)
        style = {"completed": "v.ok", "failed": "v.err", "max_iterations": "v.warn"}.get(status, "v.meta")
        self.console.print()
        self.console.print(Text(f"{status} {GLYPHS.sep} {iterations} iteration(s)", style=style))
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
                    ("err ", "v.tool.err") if is_err else ("ok  ", "v.tool.ok"), (name, "v.tool")
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
            return model_markdown(text), "thinking"

        return None, "thinking"


def _binding_name(stack: Any, contract: str) -> str:
    b = stack.binding_for(contract)
    return b.manifest.name if b else "(none)"


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
