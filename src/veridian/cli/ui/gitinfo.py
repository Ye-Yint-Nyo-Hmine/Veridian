"""The current git branch, read straight from ``.git`` — no subprocess.

The status line names the branch the workspace is on. Shelling out to ``git`` would be a process
spawn on every prompt and a dependency on ``git`` being on PATH; reading ``.git/HEAD`` is neither,
and it works the same on a Windows host. Returns ``None`` when the workspace is not a git
checkout, so the caller renders the field as absent rather than empty.
"""

from __future__ import annotations

from pathlib import Path


def _git_dir(root: Path) -> Path | None:
    """The ``.git`` directory for ``root`` — following a ``.git`` *file* (worktree / submodule)."""
    dot = root / ".git"
    if dot.is_dir():
        return dot
    if dot.is_file():
        try:
            line = dot.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if line.startswith("gitdir:"):
            p = Path(line[len("gitdir:"):].strip())
            if not p.is_absolute():
                p = (root / p).resolve()
            return p if p.is_dir() else None
    return None


def current_branch(root: Path) -> str | None:
    """Branch name for the checkout at ``root``; a short SHA when detached; ``None`` when not a
    git checkout."""
    gd = _git_dir(Path(root))
    if gd is None:
        return None
    try:
        head = (gd / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[4:].strip()
        return ref.rsplit("/", 1)[-1] if ref else None
    return head[:7] or None  # detached HEAD — a raw commit SHA
