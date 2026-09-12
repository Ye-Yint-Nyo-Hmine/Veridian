"""Locating the Veridian distribution tree — the directory holding ``schemas/``, ``bricks/``,
``stacks/``, ``pre-installed/``, and ``sdks/``.

Until the installer existed there was only one answer: walk up from the current working directory
until a git checkout appeared. That works for a contributor and fails for everyone else, because a
globally installed ``veridian`` is run from the user's own project directory, which is not a
checkout. This module adds the missing answer without disturbing the first one.

Resolution order (:func:`veridian_root`), first match wins:

1. ``$VERIDIAN_ROOT`` — an explicit override, validated rather than trusted.
2. A **development checkout**, by walking up from ``start`` / the cwd. Unchanged from the original
   behaviour, and deliberately ahead of the installed tree so that working inside a checkout keeps
   using that checkout even on a machine that also has Veridian installed.
3. The **installed tree**, via ``$VERIDIAN_HOME/current`` naming a version under ``versions/``.

Only a real Veridian checkout satisfies step 2's test, so an unrelated ``pyproject.toml`` in some
user's project can never shadow the installed tree.

This module imports nothing from Veridian, by necessity: :mod:`veridian.contracts._schemas` needs
it, and reaching :mod:`veridian.plugin_runtime.home` from there would execute
``plugin_runtime/__init__`` → ``manifest`` → ``veridian.contracts`` and cycle back into a
half-initialised ``_schemas``. The handful of lines resolving ``VERIDIAN_HOME`` are therefore
duplicated here; :func:`veridian.plugin_runtime.home.veridian_home` remains the one everything
else calls. Nothing here touches the filesystem at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_ROOT = "VERIDIAN_ROOT"
ENV_HOME = "VERIDIAN_HOME"

DEFAULT_HOME_DIRNAME = ".veridian"
VERSIONS_DIRNAME = "versions"
APP_DIRNAME = "app"
CURRENT_FILENAME = "current"


def _home() -> Path | None:
    """``VERIDIAN_HOME``, or ``~/.veridian``. ``None`` when the platform has no home directory."""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()
    try:
        return (Path.home() / DEFAULT_HOME_DIRNAME).resolve()
    except (RuntimeError, OSError):
        return None


def is_dist_root(path: Path) -> bool:
    """Does ``path`` hold a usable distribution tree? Used for roots we are *told* about."""
    p = Path(path)
    return (p / "schemas" / "protocol").is_dir() and (p / "bricks").is_dir()


def _is_checkout(path: Path) -> bool:
    """The original ``find_repo_root`` test, preserved exactly so the walk's behaviour is
    unchanged: a source tree carries both the schemas and the project file."""
    return (path / "schemas" / "protocol").is_dir() and (path / "pyproject.toml").is_file()


def installed_root() -> Path | None:
    """The active installed tree: ``$VERIDIAN_HOME/versions/<current>/app``, or ``None``.

    ``current`` is a one-line text file naming the active version — a pointer file rather than a
    symlink, since symlinks need elevation or Developer Mode on Windows. Writing it is the last,
    atomic step of an install, so a half-installed version is never selected.
    """
    home = _home()
    if home is None:
        return None
    try:
        version = (home / CURRENT_FILENAME).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    # The pointer names one directory under versions/, never a path.
    if not version or version in (".", "..") or "/" in version or "\\" in version:
        return None
    root = home / VERSIONS_DIRNAME / version / APP_DIRNAME
    return root if is_dist_root(root) else None


def veridian_root(start: Path | None = None) -> Path | None:
    """The distribution tree, or ``None`` if there is neither a checkout nor an install.

    Raises ``RuntimeError`` if ``$VERIDIAN_ROOT`` is set but does not point at a usable tree —
    an explicit override that silently fell back would be worse than a clear failure.
    """
    override = os.environ.get(ENV_ROOT)
    if override:
        p = Path(os.path.expandvars(override)).expanduser().resolve()
        if not is_dist_root(p):
            raise RuntimeError(
                f"{ENV_ROOT}={p} is not a Veridian root (expected schemas/protocol/ and bricks/ under it)"
            )
        return p

    here = (start or Path.cwd()).resolve()
    for base in (here, *here.parents):
        if _is_checkout(base):
            return base

    return installed_root()


def install_mode(start: Path | None = None) -> str:
    """How the running Veridian found its tree, for ``doctor`` and ``--version``."""
    if os.environ.get(ENV_ROOT):
        return ENV_ROOT
    here = (start or Path.cwd()).resolve()
    if any(_is_checkout(base) for base in (here, *here.parents)):
        return "dev checkout"
    return "installed" if installed_root() is not None else "not found"
