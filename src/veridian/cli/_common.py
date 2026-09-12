"""Small shared helpers for the CLI."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from rich.console import Console

from veridian.cli.ui.theme import THEME
from veridian.kernel.config import find_repo_root
from veridian.plugin_runtime.search import (
    SearchRoot,
    brick_search_roots,
    stack_search_roots,
)

# Best effort: on Windows a piped stdout defaults to the ANSI codepage (cp1252) with strict
# error handling, so a stray non-ASCII byte in tool output would crash the write. UTF-8 with
# replacement keeps output flowing; a real console is already UTF-8 and unaffected.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass

console = Console(theme=THEME)
err_console = Console(stderr=True, theme=THEME)


def repo_root() -> Path:
    return find_repo_root(Path.cwd())


def bricks_root() -> Path:
    return repo_root() / "bricks"


def stacks_root() -> Path:
    return repo_root() / "stacks"


def brick_roots() -> list[SearchRoot]:
    """The ordered brick search roots: project ``./bricks``, then ``VERIDIAN_HOME/bricks``, then
    the repo's built-in ``bricks/``. First match wins."""
    return brick_search_roots(repo_root=repo_root())


def stack_roots() -> list[SearchRoot]:
    return stack_search_roots(repo_root=repo_root())


def default_stack() -> Path:
    """The stack a bare ``veridian`` uses: whatever the first run recorded, else the built-in one.

    The recorded name is resolved through the ordinary stack search roots, so a stack the first run
    wrote to ``VERIDIAN_HOME/stacks`` is found the same way any installed stack is.
    """
    from veridian.cli.userconfig import read_config
    from veridian.kernel.config import resolve_stack_ref

    chosen = read_config().get("default_stack")
    if chosen:
        try:
            return resolve_stack_ref(chosen)
        except Exception:  # noqa: BLE001 — a stale choice must not block the built-in fallback
            pass
    return stacks_root() / "default.toml"


def missing_provider_env(resolved) -> list[str]:
    """The credential variable an inference binding declares but does not have.

    Deliberately declarative rather than guessed: a binding states which variable carries its key
    with ``api_key_env``, the provider brick reads that same variable, and this reads it to warn
    before the first goal instead of after. A binding that declares nothing needs nothing — which
    is what a local model server is, and why guessing from the manifest would be wrong there.
    """
    binding = resolved.binding_for("inference")
    if binding is None:
        return []
    var = binding.config.get("api_key_env")
    if not var or binding.config.get("api_key") or os.environ.get(var):
        return []
    return [str(var)]


def describe_run_failure(exc) -> str:
    """One line for a run that failed inside a brick: which brick, and where the full traceback
    is. ``exc`` is the :class:`ProtocolError` surfaced by the orchestrator run.

    A brick handler crash arrives with ``data`` carrying ``brick`` / ``method`` / ``traceback``
    (the SDK attaches it and also writes the traceback to ``VERIDIAN_HOME/logs/brick-errors.log``).
    Without that a run failure was just ``run failed: TypeError: ...`` with nothing to go on.
    Set ``VERIDIAN_DEBUG`` to also print the traceback inline.
    """
    data = getattr(exc, "data", None) or {}
    brick = data.get("brick")
    msg = getattr(exc, "message", None) or str(exc)
    head = f"run failed in brick {brick!r}: {msg}" if brick else f"run failed: {msg}"

    tb = data.get("traceback")
    if not tb:
        return head

    if os.environ.get("VERIDIAN_DEBUG"):
        return f"{head}\n{tb.rstrip()}"

    # Point at the log the SDK already wrote; fall back to writing our own copy if reading the
    # home dir is possible here but the brick could not write it (e.g. a container brick).
    try:
        from veridian.plugin_runtime.home import veridian_home

        path = veridian_home() / "logs" / "brick-errors.log"
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            with path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n===== {stamp}  {brick}  {data.get('method', '?')} =====\n{tb}")
        return f"{head}\n  full traceback: {path}  (or set VERIDIAN_DEBUG=1 to print it here)"
    except Exception:  # noqa: BLE001
        return f"{head}\n  set VERIDIAN_DEBUG=1 to print the full traceback"


def model_name(resolved) -> str:
    """The model the inference binding will use, for the boot header and status line. Falls back
    to the brick's short name, then ``?`` — never fabricates a model id."""
    try:
        binding = resolved.binding_for("inference")
    except AttributeError:
        return "?"
    if binding is None:
        return "?"
    model = binding.config.get("model")
    if model:
        return str(model)
    return binding.manifest.name.split("/")[-1]
