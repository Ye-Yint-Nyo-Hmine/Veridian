"""Stack configuration: load a stack TOML file, validate it, and resolve every binding to a real
brick manifest. The kernel loads exactly one stack per run."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian import __version__
from veridian._paths import veridian_root
from veridian.contracts import validate_document
from veridian.contracts._schemas import SchemaValidationError
from veridian.kernel.errors import StackConfigError
from veridian.plugin_runtime.loader import resolve_brick_ref
from veridian.plugin_runtime.manifest import IncompatibleVeridianVersion, Manifest
from veridian.plugin_runtime.search import brick_search_roots, stack_search_roots
from veridian.security.policy import Policy


def find_repo_root(start: Path | None = None) -> Path:
    """The distribution tree: a development checkout above ``start``, else the installed tree.

    See :mod:`veridian._paths` for the full order. The cwd fallback is kept so callers that only
    want *somewhere* to resolve a relative path behave as they always have.
    """
    return veridian_root(start) or (start or Path.cwd()).resolve()


def resolve_stack_ref(ref: str | Path, *, repo_root: Path | None = None) -> Path:
    """Resolve a ``--stack`` value that may be a path *or* a bare name.

    A path (absolute or relative) that exists is used directly. Otherwise ``<ref>.toml`` is looked
    for under the ordered stack roots (project ``./stacks``, then ``VERIDIAN_HOME/stacks``, then the
    repo's ``stacks/``); failing that, every ``*.toml`` in those roots is scanned for a matching
    ``[stack].name``. First match wins."""
    p = Path(ref)
    if p.is_file():
        return p.resolve()
    repo_root = repo_root or find_repo_root()
    roots = stack_search_roots(repo_root=repo_root)
    name = str(ref)
    for root in roots:
        cand = root.path / f"{name}.toml"
        if cand.is_file():
            return cand.resolve()
    for root in roots:
        if not root.path.is_dir():
            continue
        for f in sorted(root.path.glob("*.toml")):
            try:
                raw = tomllib.loads(f.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            if raw.get("stack", {}).get("name") == name:
                return f.resolve()
    tried = [str(r.path) for r in roots]
    raise StackConfigError(f"no stack named {name!r} (looked for {name}.toml under {tried})")


@dataclass(frozen=True)
class ResolvedBinding:
    contract: str
    manifest: Manifest
    config: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    disabled: bool = False


@dataclass(frozen=True)
class ResolvedStack:
    name: str
    description: str
    path: Path
    bindings: list[ResolvedBinding]
    policy: Policy
    raw: dict[str, Any]

    def active(self) -> list[ResolvedBinding]:
        return [b for b in self.bindings if not b.disabled]

    def bound_map(self) -> list[str]:
        return [b.contract for b in self.bindings if not b.disabled]

    def binding_for(self, contract: str) -> ResolvedBinding | None:
        return next((b for b in self.bindings if b.contract == contract), None)


def _normalise_binding(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, str):
        return {"brick": value}
    return dict(value)


def load_stack(path: Path, *, repo_root: Path | None = None) -> ResolvedStack:
    path = Path(path).resolve()
    if not path.is_file():
        raise StackConfigError(f"stack file not found: {path}")
    repo_root = repo_root or find_repo_root(path.parent)

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise StackConfigError(f"{path}: invalid TOML: {exc}") from exc

    try:
        validate_document("configuration.schema.json", raw)
    except SchemaValidationError as exc:
        raise StackConfigError(f"{path}: {exc}") from exc

    raw_bindings = {c: _normalise_binding(v) for c, v in raw["bindings"].items()}
    roots = brick_search_roots(repo_root=repo_root)
    search_roots = [(r.label, r.path) for r in roots]

    resolved: list[ResolvedBinding] = []
    for contract, b in raw_bindings.items():
        ref = b["brick"]
        try:
            manifest = resolve_brick_ref(
                ref, search_roots=search_roots, repo_root=repo_root
            ).manifest
        except FileNotFoundError as exc:
            raise StackConfigError(f"{path}: binding {contract!r}: {exc}") from exc
        try:
            manifest.check_veridian_compat(__version__)
        except IncompatibleVeridianVersion as exc:
            raise StackConfigError(f"{path}: binding {contract!r}: {exc}") from exc
        if not manifest.declares(contract) and not b.get("disabled"):
            raise StackConfigError(
                f"{path}: binding {contract!r} -> {manifest.name} but that brick's manifest does "
                f"not implement {contract!r} (implements {sorted(manifest.implements)})"
            )
        resolved.append(
            ResolvedBinding(
                contract=contract,
                manifest=manifest,
                config=dict(b.get("config", {})),
                env=dict(b.get("env", {})),
                disabled=bool(b.get("disabled", False)),
            )
        )

    policy = Policy.from_config(raw, raw_bindings)
    stack = raw.get("stack", {})
    return ResolvedStack(
        name=stack.get("name", path.stem),
        description=stack.get("description", ""),
        path=path,
        bindings=resolved,
        policy=policy,
        raw=raw,
    )
