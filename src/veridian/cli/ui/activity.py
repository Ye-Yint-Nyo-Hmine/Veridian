"""The working/thinking indicator.

A single static line written to stderr while the agent is busy and no output is arriving. No
spinner, no animation: it is printed once, carries a label saying what is being waited on, and is
erased the moment real output or the next prompt takes its place. On a non-TTY it prints nothing —
piped output stays clean.
"""

from __future__ import annotations

from rich.console import Console


class Activity:
    def __init__(self, console: Console) -> None:
        self._console = console
        self._shown: str | None = None

    @property
    def active(self) -> bool:
        return self._shown is not None

    def show(self, label: str) -> None:
        """Display ``label`` as the current activity, replacing any previous one."""
        if not self._console.is_terminal:
            return
        if self._shown == label:
            return
        self.clear()
        self._console.file.write(f"\x1b[2m{label}…\x1b[0m")
        self._console.file.flush()
        self._shown = label

    def clear(self) -> None:
        """Erase the indicator line if one is showing."""
        if self._shown is None:
            return
        if self._console.is_terminal:
            self._console.file.write("\r\x1b[2K")
            self._console.file.flush()
        self._shown = None
