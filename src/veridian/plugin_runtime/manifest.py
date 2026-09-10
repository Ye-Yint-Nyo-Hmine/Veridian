"""Brick manifest parsing and validation. ``veridian.toml`` at the root of a brick directory."""

from __future__ import annotations

import hashlib
import json
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


class UnresolvedEnvironment(RuntimeError):
    """A brick declares a ``[dependencies]`` table but has no private environment that matches it:
    it was never installed (``missing``) or the recorded environment no longer matches the
    manifest / has lost its interpreter (``stale``).

    Raised instead of silently falling back to the kernel's interpreter. A brick that pins
    ``six==1.15.0`` and shares whatever the kernel happens to have is precisely the failure
    dependency isolation exists to prevent, so the fall-back is reserved for bricks that declare
    no dependencies at all.
    """

    def __init__(self, brick: str, status: str) -> None:
        self.brick = brick
        self.status = status  # "missing" | "stale"
        super().__init__(
            f"{brick}: declares [dependencies] but its private environment is {status}; "
            f"run `veridian brick install {brick}` "
            f"(refusing to fall back to the kernel interpreter)"
        )

# Where `veridian brick install` records a brick's resolved private environment. Kept inside the
# brick directory (already git-ignored) so the environment travels with the brick and nothing
# central has to be consulted at spawn time.
ENV_DIRNAME = ".veridian"
ENV_RECORD_FILENAME = "environment.json"


def dependency_fingerprint(dependencies: dict[str, Any]) -> str:
    """A stable short hash of a manifest's ``[dependencies]`` table. The install step stamps the
    resolved environment with this; :meth:`Manifest.resolved_command` only trusts a recorded
    interpreter whose fingerprint still matches, so editing the table invalidates a stale venv."""
    canonical = json.dumps(dependencies or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


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
    dependencies: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def declares(self, contract: str, method: str | None = None) -> bool:
        if contract not in self.implements:
            return False
        return method is None or method in self.implements[contract]

    def needs_isolated_env(self) -> bool:
        """True when this brick declares third-party dependencies and therefore must be resolved
        into its own environment rather than sharing the kernel's interpreter."""
        deps = self.dependencies
        return bool(deps.get("python") or deps.get("node") or deps.get("python_version"))

    @property
    def env_record_path(self) -> Path:
        return self.directory / ENV_DIRNAME / ENV_RECORD_FILENAME

    def load_env_record(self) -> dict[str, Any] | None:
        """The environment stamped by the last successful ``veridian brick install``, or ``None``
        if the brick was never installed."""
        try:
            return json.loads(self.env_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def environment_state(self) -> str:
        """``"n/a"`` (no dependencies), ``"ok"`` (installed and current), ``"missing"`` (declared
        but never installed), or ``"stale"`` (installed against a different dependency table, or
        the recorded interpreter is gone). :func:`environments.environment_status` is the public
        entry point and delegates here."""
        if not self.needs_isolated_env():
            return "n/a"
        record = self.load_env_record()
        if not record:
            return "missing"
        if record.get("fingerprint") != dependency_fingerprint(self.dependencies):
            return "stale"
        if record.get("runtime") == "python":
            interp = record.get("interpreter")
            if not interp or not Path(interp).exists():
                return "stale"
        return "ok"

    def resolved_interpreter(self) -> str:
        """The interpreter ``${python}`` expands to for *this* brick.

        * A brick with **no** ``[dependencies]`` table gets the kernel's own interpreter, exactly
          as in Milestone 1.
        * A brick with a private environment whose fingerprint still matches its manifest gets
          that venv's python.
        * A brick that declares dependencies but has no matching environment
          (``missing``/``stale``) raises :class:`UnresolvedEnvironment` rather than silently
          borrowing the kernel's interpreter.
        """
        if not self.needs_isolated_env():
            return sys.executable
        state = self.environment_state()
        if state != "ok":
            raise UnresolvedEnvironment(self.name, state)
        record = self.load_env_record() or {}
        if record.get("runtime") == "python":
            return record["interpreter"]
        # A non-python isolated env (node): its spawn command drives ${node}, not ${python}.
        return sys.executable

    def resolved_command(self) -> list[str]:
        """Build the argv to spawn.

        * ``${python}`` -> this brick's resolved interpreter: its private venv when it declares
          ``[dependencies]`` and has been installed, otherwise the kernel's own interpreter.
        * ``${node}`` -> the ``node`` on PATH (a node brick with dependencies resolves its
          ``node_modules`` locally, next to its entrypoint, by Node's own algorithm).
        * A relative argument that names a file inside the brick directory is made absolute, so the
          brick can be launched with any working directory (bricks run confined to the workspace
          root, never their own directory).
        """
        subs = {"${python}": self.resolved_interpreter(), "${node}": shutil.which("node") or "node"}
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
        dependencies=dict(data.get("dependencies", {})),
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
