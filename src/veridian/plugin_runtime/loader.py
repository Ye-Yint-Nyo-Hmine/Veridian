"""Brick discovery. A brick is a directory with a ``veridian.toml`` — never an importable module."""

from __future__ import annotations

from pathlib import Path

from veridian.contracts.errors import ProtocolError
from veridian.plugin_runtime.manifest import MANIFEST_FILENAME, Manifest, load_manifest

# Directories never worth descending into while scanning for bricks.
_SKIP = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache"}


def _brick_dirs(root: Path) -> list[Path]:
    root = Path(root).resolve()
    if not root.is_dir():
        return []
    out: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        if (current / MANIFEST_FILENAME).is_file():
            out.append(current)
            continue
        for child in sorted(current.iterdir()):
            if child.is_dir() and child.name not in _SKIP:
                stack.append(child)
    return out


def discover(root: Path) -> dict[str, Manifest]:
    """Every *valid* brick under ``root``, keyed by manifest name. A directory with a
    ``veridian.toml`` is a brick and is not descended into further. Unparseable manifests are
    skipped here; use :func:`discover_with_errors` to see them."""
    found, _ = discover_with_errors(root)
    return found


def discover_with_errors(root: Path) -> tuple[dict[str, Manifest], list[tuple[Path, ProtocolError]]]:
    found: dict[str, Manifest] = {}
    errors: list[tuple[Path, ProtocolError]] = []
    for brick_dir in _brick_dirs(root):
        try:
            manifest = load_manifest(brick_dir)
        except ProtocolError as exc:
            errors.append((brick_dir, exc))
        else:
            found[manifest.name] = manifest
    return found, errors


def resolve_brick(ref: str, *, search_roots: list[Path], repo_root: Path | None = None) -> Manifest:
    """Resolve a stack-file brick reference. ``ref`` is either a path (absolute, or relative to
    ``repo_root`` / the cwd) that contains a ``veridian.toml``, or a manifest name found under one
    of ``search_roots``."""
    candidates: list[Path] = []
    p = Path(ref)
    if p.is_absolute():
        candidates.append(p)
    else:
        if repo_root:
            candidates.append(repo_root / ref)
        candidates.append(Path.cwd() / ref)
    for cand in candidates:
        if (cand / MANIFEST_FILENAME).is_file():
            return load_manifest(cand)

    for search_root in search_roots:
        table = discover(search_root)
        if ref in table:
            return table[ref]

    raise FileNotFoundError(
        f"could not resolve brick {ref!r}: not a directory with {MANIFEST_FILENAME}, "
        f"and no manifest named {ref!r} under {[str(s) for s in search_roots]}"
    )
