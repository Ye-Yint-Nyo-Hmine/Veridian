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


class IncompatibleVeridianVersion(RuntimeError):
    """A brick's ``[requires].veridian`` specifier does not admit the running Veridian version.

    Raised while a stack resolves its bindings — before any brick is spawned — so the failure
    names both the brick's requirement and the version actually running, instead of surfacing
    later as an obscure runtime error.
    """

    def __init__(self, brick: str, requirement: str, running: str) -> None:
        self.brick = brick
        self.requirement = requirement
        self.running = running
        super().__init__(
            f"{brick}: requires Veridian {requirement!r} but this is Veridian {running}; "
            f"install a compatible version of the brick or upgrade Veridian"
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
class IsolationSpec:
    """The manifest's ``[isolation]`` table, normalised.

    ``mode == "process"`` (the default, and the whole of Milestone 1) is a plain subprocess.
    ``mode == "container"`` runs the brick inside a container the kernel drives, where ``network``
    and ``allow_hosts`` become an OS-level egress boundary rather than an advisory capability.
    """

    mode: str = "process"
    image: str | None = None
    engine: str | None = None  # None -> auto-detect (docker, then podman)
    network: bool = False
    allow_hosts: tuple[str, ...] = ()

    @property
    def is_container(self) -> bool:
        return self.mode == "container"

    @property
    def unrestricted_egress(self) -> bool:
        """``network = true`` with no allowlist: the brick may reach anything. Legal, but a brick
        that also handles conversation content must never be spawned this way (enforced in the
        registry)."""
        return self.is_container and self.network and not self.allow_hosts


_PROCESS_ISOLATION = IsolationSpec()

# Contracts whose payloads carry the user's system prompt, memory, conversation, or workspace
# content. A brick implementing any of these must never run with unrestricted egress.
_CONTENT_CONTRACTS = frozenset(
    {"inference", "model_provider", "orchestrator", "context", "memory", "planner", "conversation", "workspace"}
)


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
    requires_veridian: str | None = None
    env_passthrough: list[str] = field(default_factory=list)
    optional_dependencies: list[str] = field(default_factory=list)
    dependencies: dict[str, Any] = field(default_factory=dict)
    isolation: IsolationSpec = _PROCESS_ISOLATION
    raw: dict[str, Any] = field(default_factory=dict)

    def declares(self, contract: str, method: str | None = None) -> bool:
        if contract not in self.implements:
            return False
        return method is None or method in self.implements[contract]

    def check_veridian_compat(self, running_version: str) -> None:
        """Raise :class:`IncompatibleVeridianVersion` when ``[requires].veridian`` excludes
        ``running_version``. A no-op when the brick declares no requirement."""
        if not self.requires_veridian:
            return
        from veridian.plugin_runtime.versions import InvalidVersionSpec, satisfies

        try:
            ok = satisfies(running_version, self.requires_veridian)
        except InvalidVersionSpec as exc:
            raise IncompatibleVeridianVersion(
                self.name, self.requires_veridian, running_version
            ) from exc
        if not ok:
            raise IncompatibleVeridianVersion(
                self.name, self.requires_veridian, running_version
            )

    def assert_egress_sane(self) -> None:
        """Refuse ``isolation.network = true`` with no ``allow_hosts`` for a brick that also handles
        conversation content. Unrestricted egress is legal in the abstract, but a content brick that
        can reach anything breaks the Milestone 2 bar that every network destination in a stack is
        enumerable from the manifests alone. ``network = true`` *with* an allowlist is fine."""
        if not self.isolation.unrestricted_egress:
            return
        content = sorted(set(self.implements) & _CONTENT_CONTRACTS)
        if content:
            raise ProtocolError(
                INVALID_MANIFEST,
                f"{self.name}: isolation.network = true with no allow_hosts grants unrestricted "
                f"egress, but this brick implements {content} and handles conversation content; "
                f"declare an isolation.allow_hosts allowlist instead",
                {"brick": self.name, "contracts": content},
            )

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
    iso_raw = data.get("isolation")
    if iso_raw:
        isolation = IsolationSpec(
            mode=iso_raw.get("mode", "process"),
            image=iso_raw.get("image"),
            engine=iso_raw.get("engine"),
            network=bool(iso_raw.get("network", False)),
            allow_hosts=tuple(iso_raw.get("allow_hosts", [])),
        )
        if isolation.is_container and not isolation.image:
            raise ProtocolError(
                INVALID_MANIFEST,
                f"{directory / MANIFEST_FILENAME}: isolation.mode = \"container\" requires isolation.image",
            )
    else:
        isolation = _PROCESS_ISOLATION
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
        requires_veridian=(data.get("requires") or {}).get("veridian"),
        env_passthrough=list(data.get("env_passthrough", [])),
        optional_dependencies=list(data.get("optional_dependencies", [])),
        dependencies=dict(data.get("dependencies", {})),
        isolation=isolation,
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
