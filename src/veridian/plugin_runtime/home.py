"""``VERIDIAN_HOME`` — the user-level location for installed bricks and stacks.

Milestone 1 knew exactly one place a brick could live: ``<repo>/bricks``. Anything else had to be
named by an explicit filesystem path. This module adds a second, user-owned location so a brick or
stack acquired from elsewhere has somewhere to be installed to.

* Default: ``~/.veridian``.
* Override: the ``VERIDIAN_HOME`` environment variable (``~`` and ``$VAR`` are expanded).
* Layout: ``bricks/`` and ``stacks/`` underneath it.

The directory is created **on demand** by :func:`ensure_home` (called by the acquisition commands),
never at import time — importing Veridian must not touch the filesystem. Every part of the codebase
that needs the location calls :func:`veridian_home`; nothing else calls ``Path.home()``.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_VAR = "VERIDIAN_HOME"
DEFAULT_DIRNAME = ".veridian"


def veridian_home() -> Path:
    """Resolve ``VERIDIAN_HOME``: the env var if set, otherwise ``~/.veridian``. Absolute, with
    ``~`` and environment variables expanded. Does not create anything."""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(os.path.expandvars(override)).expanduser().resolve()
    return (Path.home() / DEFAULT_DIRNAME).resolve()


def home_bricks() -> Path:
    return veridian_home() / "bricks"


def home_stacks() -> Path:
    return veridian_home() / "stacks"


def ensure_home() -> Path:
    """Create ``VERIDIAN_HOME/{bricks,stacks}`` if absent and return the home path."""
    home = veridian_home()
    (home / "bricks").mkdir(parents=True, exist_ok=True)
    (home / "stacks").mkdir(parents=True, exist_ok=True)
    return home
