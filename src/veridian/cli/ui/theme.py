"""Semantic styles for the interactive CLI.

Colour here is never decorative: each style names one kind of information so the eye can separate
user input from model prose from tool activity from status. Keep the palette small.
"""

from __future__ import annotations

import sys

from rich.theme import Theme

# One entry per semantic role. Renderers reference these names, never raw colours.
STYLES: dict[str, str] = {
    "v.prompt": "bold",           # the input caret
    "v.user": "bold",             # the goal the user just entered
    "v.meta": "dim",              # labels, paths, counts — context, not content
    "v.rule": "dim",              # turn separators
    "v.status": "dim",            # the working/thinking indicator
    "v.step": "cyan",             # an orchestrator step becoming active
    "v.plan": "cyan",             # the plan outline
    "v.tool": "cyan",             # a tool call
    "v.tool.ok": "green",         # a tool that returned cleanly
    "v.tool.err": "red",          # a tool that returned an error
    "v.ok": "green",              # run completed
    "v.warn": "yellow",           # run hit a soft limit / was interrupted
    "v.err": "bold red",          # a hard error
    "v.logo": "#40826D",          # the braille wordmark — viridian green, the one decorative mark
}

THEME = Theme(STYLES)


def _can_encode(text: str) -> bool:
    """Whether the current stdout encoding can carry ``text``.

    Resolved on each call, not at import, so a stdout reconfigured to UTF-8 after start counts.
    A piped stdout under a legacy Windows codepage (cp1252) cannot encode braille or box glyphs;
    everything then falls back to ASCII.
    """
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
    except (LookupError, UnicodeEncodeError):
        return False
    return True


# The wordmark. Braille where stdout can encode it, a plain ASCII wordmark everywhere else. It is
# the single exception to "every mark carries information" — identity, nothing more.
LOGO_UNICODE: tuple[str, ...] = (
    " ⡇⢸ ⣏⡉ ⣏⡱ ⡇ ⡏⢱ ⡇ ⣎⣱ ⡷⣸",
    " ⠸⠃ ⠧⠤ ⠇⠱ ⠇ ⠧⠜ ⠇ ⠇⠸ ⠇⠹",
)
LOGO_ASCII: tuple[str, ...] = ("V E R I D I A N",)


def logo() -> tuple[str, ...]:
    """The wordmark lines to print, resolved against what stdout can encode."""
    return LOGO_UNICODE if _can_encode("".join(LOGO_UNICODE)) else LOGO_ASCII

# Code fences inside model Markdown. A terminal-native, low-contrast scheme — readable in both
# light and dark terminals, not flashy.
CODE_THEME = "ansi_dark"


class _Glyphs:
    """Structural marks. The Unicode set on a terminal that can encode it, ASCII everywhere else
    (a piped stdout under a legacy Windows codepage, for one)."""

    _UNICODE = {
        "caret": "›", "bullet": "·", "arrow": "→", "sep": "·",
        "response": "◈", "nest": "∟", "done": "↠", "mode": "⏵⏵",
    }
    _ASCII = {
        "caret": ">", "bullet": "-", "arrow": "->", "sep": "-",
        "response": "*", "nest": "\\_", "done": "=>", "mode": ">>",
    }

    def _resolve(self) -> dict[str, str]:
        # Resolved on first use, not at import, so a stdout reconfigured to UTF-8 counts.
        return self._UNICODE if _can_encode("".join(self._UNICODE.values())) else self._ASCII

    def __getattr__(self, name: str) -> str:
        if name not in self._UNICODE:
            raise AttributeError(name)
        return self._resolve()[name]


GLYPHS = _Glyphs()
