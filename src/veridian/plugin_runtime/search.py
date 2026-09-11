"""Ordered brick / stack search roots.

Milestone 1 resolved a brick name against exactly one directory, ``<repo>/bricks``. That is now
the *last* entry in an explicit precedence list:

1. ``project`` — ``./bricks`` next to where the command runs (a project vendoring its own bricks)
2. ``user`` — ``VERIDIAN_HOME/bricks`` (bricks the user installed with ``veridian brick add``)
3. ``builtin`` — ``<repo>/bricks`` (the bricks Veridian ships)

First match wins. The order is fixed, documented, and printable (``veridian brick which``) because
a user debugging *which* copy of a brick actually loaded needs to see the list the resolver walked.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from veridian.plugin_runtime.home import home_bricks, home_stacks


@dataclass(frozen=True)
class SearchRoot:
    """One entry in the precedence list: a short stable label and the directory it points at.
    ``exists`` is a convenience for display — a root that is not present is still listed, so the
    precedence order is always visible."""

    label: str
    path: Path

    @property
    def exists(self) -> bool:
        return self.path.is_dir()


def brick_search_roots(*, repo_root: Path, project_dir: Path | None = None) -> list[SearchRoot]:
    """The ordered roots a brick *name* is resolved against. ``project_dir`` defaults to the
    current working directory."""
    project = (project_dir or Path.cwd()).resolve()
    return [
        SearchRoot("project", project / "bricks"),
        SearchRoot("user", home_bricks()),
        SearchRoot("builtin", (repo_root / "bricks").resolve()),
    ]


def stack_search_roots(*, repo_root: Path, project_dir: Path | None = None) -> list[SearchRoot]:
    """The ordered roots a stack ``--stack <name>`` is resolved against, same precedence as
    bricks: project-local, then user, then the stacks Veridian ships."""
    project = (project_dir or Path.cwd()).resolve()
    return [
        SearchRoot("project", project / "stacks"),
        SearchRoot("user", home_stacks()),
        SearchRoot("builtin", (repo_root / "stacks").resolve()),
    ]
