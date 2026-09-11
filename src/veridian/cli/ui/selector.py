"""A reusable numbered selector.

Given a list of already-formatted labels, print them numbered, read an index, and return it
zero-based — or ``None`` when the user cancels (blank line, ``q``, or Ctrl-D) or when there is no
one to ask. ``/model`` is its first consumer; ``--resume``'s session list is its second.

It must not hang when stdin is not a TTY: the list is printed once, a single line is read, and an
EOF returns ``None`` with a one-line note rather than spinning on an unfillable prompt.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from veridian.cli.ui.theme import GLYPHS

_MAX_ATTEMPTS = 3


def select(
    options: Sequence[str],
    *,
    prompt: str,
    console: Console,
    err_console: Console,
    allow_cancel: bool = True,
) -> int | None:
    """Render ``options`` numbered from 1, read a choice, return its zero-based index.

    Returns ``None`` if the user cancels, if the read hits EOF, or if ``options`` is empty.
    """
    if not options:
        return None

    for i, label in enumerate(options, 1):
        console.print(Text(f"  {i:>2}. ", style="v.meta"), end="")
        console.print(Text(label, style="v.meta"))

    hint = "  (number, or blank to cancel)" if allow_cancel else "  (number)"
    interactive = console.is_terminal and sys.stdin.isatty()
    attempts = _MAX_ATTEMPTS if interactive else 1

    for _ in range(attempts):
        console.print(Text(f"{GLYPHS.caret} {prompt}{hint}", style="v.prompt"), end=" ")
        try:
            raw = input().strip()
        except EOFError:
            if not interactive:
                err_console.print(Text("no selection — stdin is not interactive", style="v.meta"))
            else:
                console.print()
            return None

        if not raw or raw.lower() == "q":
            return None
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return n - 1
        err_console.print(Text(f"not a choice in 1..{len(options)}: {raw!r}", style="v.err"))

    return None


__all__ = ["select"]
