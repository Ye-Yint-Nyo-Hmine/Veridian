"""Brick manifest parsing and validation. ``veridian.toml`` at the root of a brick directory."""

from __future__ import annotations

import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.contracts import validate_document
from veridian.contracts._schemas import SchemaValidationError
from veridian.contracts.errors import INVALID_MANIFEST, ProtocolError

MANIFEST_FILENAME = "veridian.toml"


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    protocol: str
    spawn_command: list[str]
    implements: dict[str, list[str]]
    directory: Path
    description: str = ""
    runtime: str = "other"
    spawn_cwd: str | None = None
    requires: list[str] = field(default_factory=list)
    env_passthrough: list[str] = field(default_factory=list)
    optional_dependencies: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def declares(self, contract: str, method: str | None = None) -> bool:
        if contract not in self.implements:
            return False
        return method is None or method in self.implements[contract]

    def resolved_command(self) -> list[str]:
        """Build the argv to spawn.

        * ``${python}`` -> the kernel's own interpreter (bricks run under the same Python).
        * ``${node}`` -> the ``node`` on PATH.
        * A relative argument that names a file inside the brick directory is made absolute, so the
          brick can be launched with any working directory (bricks run confined to the workspace
          root, never their own directory).
        """
        subs = {"${python}": sys.executable, "${node}": shutil.which("node") or "node"}
        out: list[str] = []
        for part in self.spawn_command:
            if part in subs:
                out.append(subs[part])
                continue
            candidate = self.directory / part
            if not Path(part).is_absolute() and candidate.exists():
                out.append(str(candidate.resolve()))
            else:
                out.append(part)
        return out

    def resolved_cwd(self, workspace_root: Path) -> Path:
        if self.spawn_cwd:
            return (self.directory / self.spawn_cwd).resolve()
        return self.directory


def parse_manifest(data: dict[str, Any], directory: Path) -> Manifest:
    try:
        validate_document("plugin-manifest.schema.json", data)
    except SchemaValidationError as exc:
        raise ProtocolError(INVALID_MANIFEST, f"{directory / MANIFEST_FILENAME}: {exc}", exc.as_error_data()) from exc

    implements = {entry["contract"]: list(entry["methods"]) for entry in data["implements"]}
    caps = data.get("capabilities", {})
    return Manifest(
        name=data["name"],
        version=data["version"],
        protocol=data["protocol"],
        spawn_command=list(data["spawn"]["command"]),
        spawn_cwd=data["spawn"].get("cwd"),
        implements=implements,
        directory=directory,
        description=data.get("description", ""),
        runtime=data.get("runtime", "other"),
        requires=list(caps.get("requires", [])),
        env_passthrough=list(data.get("env_passthrough", [])),
        optional_dependencies=list(data.get("optional_dependencies", [])),
        raw=data,
    )


def load_manifest(brick_dir: Path) -> Manifest:
    brick_dir = Path(brick_dir).resolve()
    path = brick_dir / MANIFEST_FILENAME
    if not path.is_file():
        raise ProtocolError(INVALID_MANIFEST, f"no {MANIFEST_FILENAME} in {brick_dir}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProtocolError(INVALID_MANIFEST, f"{path}: invalid TOML: {exc}") from exc
    return parse_manifest(data, brick_dir)
