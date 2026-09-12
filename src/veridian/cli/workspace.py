"""Is this directory a sensible workspace?

The workspace is whatever directory Veridian was started in, which makes one mistake very easy to
make and very hard to read once made: launching from a home directory or a filesystem root points
every workspace-scoped brick at the whole machine. Context indexing is budgeted so that degrades
instead of hanging, but a degraded agent that silently sees 2,000 of your 100,000 files is still
worth one line of warning at boot.

This is advisory. It names the problem and the fix and then gets out of the way — a user who means
it can still do it, and no brick behaviour changes.
"""

from __future__ import annotations

from pathlib import Path


def _is_filesystem_root(path: Path) -> bool:
    return path == path.parent


def workspace_warning(path: Path) -> str | None:
    """A one-line reason this directory is a poor workspace, or ``None`` if it looks fine.

    Checked: the user's home directory, a filesystem or drive root, and the directory homes are
    kept in (the Windows Users folder, ``/home``, ``/Users``), which is a root by another name.
    """
    try:
        path = path.resolve()
    except OSError:
        return None

    if _is_filesystem_root(path):
        return f"{path} is a filesystem root"

    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None

    if home is not None and path == home:
        return f"{path} is your home directory"
    if home is not None and path == home.parent:
        return f"{path} is the directory your home folder lives in"
    return None
