"""Brick discovery and name resolution. A brick is a directory with a ``veridian.toml`` — never an
importable module.

Two on-disk shapes are understood:

* **flat** — ``<root>/<anything>/veridian.toml`` (the built-in ``bricks/`` tree, and a
  project-local ``./bricks``). One copy per brick.
* **versioned** — ``<root>/<name>/<version>/veridian.toml`` (``VERIDIAN_HOME/bricks``, written by
  ``veridian brick add``). Several versions of one brick coexist; a reference may pin
  ``name@version`` and an unpinned reference takes the highest installed version.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from veridian.contracts.errors import ProtocolError
from veridian.plugin_runtime.manifest import MANIFEST_FILENAME, Manifest, load_manifest
from veridian.plugin_runtime.versions import highest

# Directories never worth descending into while scanning for bricks.
_SKIP = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache", ".veridian"}


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
    skipped here; use :func:`discover_with_errors` to see them. When a versioned root holds
    several versions of one brick, the highest wins this map."""
    found, _ = discover_with_errors(root)
    return found


def discover_with_errors(root: Path) -> tuple[dict[str, Manifest], list[tuple[Path, ProtocolError]]]:
    found: dict[str, Manifest] = {}
    errors: list[tuple[Path, ProtocolError]] = []
    by_name: dict[str, list[Manifest]] = {}
    for brick_dir in _brick_dirs(root):
        try:
            manifest = load_manifest(brick_dir)
        except ProtocolError as exc:
            errors.append((brick_dir, exc))
        else:
            by_name.setdefault(manifest.name, []).append(manifest)
    for name, manifests in by_name.items():
        if len(manifests) == 1:
            found[name] = manifests[0]
        else:
            top = highest([m.version for m in manifests])
            found[name] = next(m for m in manifests if m.version == top)
    return found, errors


def installed_versions(name: str, roots: list[Path]) -> dict[str, Manifest]:
    """Every installed version of ``name`` across ``roots`` (earlier roots win a version tie),
    keyed by version string."""
    out: dict[str, Manifest] = {}
    for root in roots:
        for brick_dir in _brick_dirs(root):
            try:
                m = load_manifest(brick_dir)
            except ProtocolError:
                continue
            if m.name == name:
                out.setdefault(m.version, m)
    return out


def _split_ref(ref: str) -> tuple[str, str | None]:
    """``"context/foo@1.2.0"`` -> ``("context/foo", "1.2.0")``; no ``@``, or an ``@`` that does not
    introduce a version-looking suffix, -> version ``None``."""
    if "@" in ref:
        name, _, version = ref.rpartition("@")
        if name and version and version[0].isdigit():
            return name, version
    return ref, None


@dataclass(frozen=True)
class ResolvedRef:
    """How a brick reference resolved: the manifest, and where it came from — a filesystem
    ``path`` the ref pointed at directly, or the ``label`` of the search root a name matched under
    (``project`` / ``user`` / ``builtin``)."""

    manifest: Manifest
    origin: str  # "path" | search-root label
    root: Path | None = None


def resolve_brick_ref(
    ref: str,
    *,
    search_roots: list[tuple[str, Path]],
    repo_root: Path | None = None,
) -> ResolvedRef:
    """Resolve ``ref`` to a brick, reporting where it was found.

    ``ref`` is either a path (absolute, or relative to ``repo_root`` / the cwd) containing a
    ``veridian.toml``, or a ``name``/``name@version`` looked up under ``search_roots`` in order —
    first match wins. ``search_roots`` is an ordered list of ``(label, path)``."""
    name, pin = _split_ref(ref)

    # A direct path reference (only when unpinned — a path already names an exact copy).
    if pin is None:
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
                return ResolvedRef(load_manifest(cand), origin="path")

    root_paths = [path for _, path in search_roots]
    for label, path in search_roots:
        versions = installed_versions(name, [path])
        if not versions:
            continue
        if pin is not None:
            if pin in versions:
                return ResolvedRef(versions[pin], origin=label, root=path)
            available = ", ".join(sorted(versions)) or "none"
            raise FileNotFoundError(
                f"brick {name!r} has no version {pin!r} under {label} root {path} "
                f"(installed: {available})"
            )
        chosen = highest(list(versions))
        return ResolvedRef(versions[chosen], origin=label, root=path)

    hint = f"{name}@{pin}" if pin else name
    raise FileNotFoundError(
        f"could not resolve brick {hint!r}: not a directory with {MANIFEST_FILENAME}, "
        f"and no brick named {name!r} under {[str(s) for s in root_paths]}"
    )


def resolve_brick(ref: str, *, search_roots: list[Path], repo_root: Path | None = None) -> Manifest:
    """Back-compatible name/path resolver. Prefer :func:`resolve_brick_ref` when the caller wants
    to know which root matched."""
    labelled = [(str(p), p) for p in search_roots]
    return resolve_brick_ref(ref, search_roots=labelled, repo_root=repo_root).manifest
