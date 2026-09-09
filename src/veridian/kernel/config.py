"""Stack configuration: load a stack TOML file, validate it, and resolve every binding to a real
brick manifest. The kernel loads exactly one stack per run."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.contracts import validate_document
from veridian.contracts._schemas import SchemaValidationError
from veridian.kernel.errors import StackConfigError
from veridian.plugin_runtime.loader import resolve_brick
from veridian.plugin_runtime.manifest import Manifest
from veridian.security.policy import Policy


def find_repo_root(start: Path | None = None) -> Path:
    here = (start or Path.cwd()).resolve()
    for base in (here, *here.parents):
        if (base / "schemas" / "protocol").is_dir() and (base / "pyproject.toml").is_file():
            return base
    return here


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
    search_roots = [repo_root / "bricks"]

    resolved: list[ResolvedBinding] = []
    for contract, b in raw_bindings.items():
        ref = b["brick"]
        try:
            manifest = resolve_brick(ref, search_roots=search_roots, repo_root=repo_root)
        except FileNotFoundError as exc:
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
