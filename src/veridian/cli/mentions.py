"""``@path`` mentions in the input line.

``@file`` or ``@"dir with spaces"`` in a goal pulls that file's contents (or that directory's
listing) into the turn. Resolution happens against the workspace root *before* the turn starts:
a path that does not resolve, or escapes the workspace, is an error that stops the turn rather
than a silent omission. Files are capped; a truncated read or a directory that was listed rather
than read is stated plainly to the user.

No tab completion — the REPL reads with a plain ``input()`` and a readline completer would put the
turn-scoped Ctrl-C / EOF handling at risk. ``/help`` says so.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

MAX_BYTES = 64 * 1024
MAX_DIR_ENTRIES = 200

# @token at a token boundary: @"quoted path" or @bare/path. Trailing sentence punctuation on a
# bare token is not part of the path.
_MENTION_RE = re.compile(r'(?<!\S)@(?:"([^"]+)"|([^\s"]+))')
_TRAILING = ".,;:!?)]}"


@dataclass
class Mention:
    raw: str
    path: str  # workspace-relative, forward slashes
    kind: str  # "file" | "dir" | "missing"
    bytes: int = 0  # byte count for a file, entry count for a dir
    truncated: bool = False
    content: str = ""
    reason: str = ""
    names: list[str] = field(default_factory=list)


def _iter_tokens(line: str):
    for m in _MENTION_RE.finditer(line):
        quoted, bare = m.group(1), m.group(2)
        if quoted is not None:
            yield quoted
        else:
            yield bare.rstrip(_TRAILING)


def resolve(line: str, workspace: Path) -> tuple[list[Mention], str | None]:
    """Resolve every ``@path`` in ``line`` against ``workspace``.

    Returns ``(resolved_mentions, error)``. ``error`` is a single message naming every mention
    that could not be resolved; when it is not ``None`` the caller must not start the turn.
    """
    ws = workspace.resolve()
    resolved: list[Mention] = []
    problems: list[str] = []

    for raw in _iter_tokens(line):
        if not raw:
            continue
        target = (Path(raw) if os.path.isabs(raw) else ws / raw)
        try:
            real = target.resolve()
        except OSError as exc:
            problems.append(f"{raw} ({exc})")
            continue

        try:
            inside = os.path.commonpath([str(real), str(ws)]) == str(ws)
        except ValueError:  # different drives on Windows, etc.
            inside = False
        if not inside:
            problems.append(f"{raw} (outside the workspace)")
            continue
        if not real.exists():
            problems.append(f"{raw} (not found)")
            continue

        rel = (real.relative_to(ws) if real != ws else Path(".")).as_posix()
        if real.is_dir():
            entries = sorted(real.iterdir())
            names = [p.name + ("/" if p.is_dir() else "") for p in entries[:MAX_DIR_ENTRIES]]
            resolved.append(
                Mention(
                    raw=raw,
                    path=rel,
                    kind="dir",
                    bytes=len(entries),
                    truncated=len(entries) > MAX_DIR_ENTRIES,
                    names=names,
                )
            )
        else:
            data = real.read_bytes()[: MAX_BYTES + 1]
            truncated = len(data) > MAX_BYTES
            text = data[:MAX_BYTES].decode("utf-8", errors="replace")
            resolved.append(
                Mention(
                    raw=raw,
                    path=rel,
                    kind="file",
                    bytes=min(len(data), MAX_BYTES),
                    truncated=truncated,
                    content=text,
                )
            )

    error = None
    if problems:
        error = "could not resolve " + "; ".join(problems)
    return resolved, error


def build_turn(goal_line: str, mentions: list[Mention]) -> str:
    """The goal string sent to ``orchestrator.run``: the user's line unchanged, then one block per
    resolved mention."""
    if not mentions:
        return goal_line
    parts = [goal_line, "", "<attached context>"]
    for m in mentions:
        if m.kind == "file":
            head = f"# @{m.path} — file, {m.bytes} bytes"
            if m.truncated:
                head += f" (truncated to {MAX_BYTES} bytes)"
            parts += [head, m.content.rstrip("\n"), ""]
        elif m.kind == "dir":
            head = f"# @{m.path} — directory, {m.bytes} entries"
            if m.truncated:
                head += f" (first {MAX_DIR_ENTRIES} listed)"
            parts += [head, *m.names, ""]
    return "\n".join(parts).rstrip() + "\n"


def notes(mentions: list[Mention]) -> list[str]:
    """One-line notices to show the user for anything that was truncated or listed-not-read."""
    out: list[str] = []
    for m in mentions:
        if m.kind == "file" and m.truncated:
            out.append(f"@{m.path}: truncated to {MAX_BYTES // 1024} KiB")
        elif m.kind == "dir":
            tail = f", first {MAX_DIR_ENTRIES} shown" if m.truncated else ""
            out.append(f"@{m.path}: directory listed ({m.bytes} entries{tail}), not read")
    return out


__all__ = ["Mention", "resolve", "build_turn", "notes", "MAX_BYTES", "MAX_DIR_ENTRIES"]
