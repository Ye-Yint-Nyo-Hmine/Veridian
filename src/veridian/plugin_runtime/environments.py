"""Per-brick dependency isolation.

Milestone 1 launched every Python brick with ``${python}`` -> the kernel's own interpreter, so
every Python brick shared one dependency set and two third-party bricks with conflicting
requirements could not coexist. The process boundary was real; the dependency boundary was not.

A brick that declares a ``[dependencies]`` table in its manifest is *installed* into its own
environment:

* **python** — a private venv under ``<brick>/.veridian/venv``, resolved with ``uv``. The venv's
  interpreter is stamped into ``<brick>/.veridian/environment.json`` together with the fingerprint
  of the dependency table, and :meth:`Manifest.resolved_command` expands ``${python}`` to it.
* **node** — a local ``<brick>/node_modules`` installed with ``npm`` from a generated
  ``package.json``. Node's own resolution algorithm picks it up from the brick's entrypoint; the
  spawn command still says ``${node}``.

Bricks with no ``[dependencies]`` table are untouched and keep running under the kernel's
interpreter, so nothing from Milestone 1 regresses.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from veridian.plugin_runtime.manifest import (
    ENV_DIRNAME,
    ENV_RECORD_FILENAME,
    Manifest,
    dependency_fingerprint,
)


class EnvironmentError_(RuntimeError):
    """A brick environment could not be resolved (tool missing, resolution failed)."""


@dataclass(frozen=True)
class EnvResult:
    brick: str
    runtime: str
    action: str  # "created" | "reused" | "skipped"
    interpreter: str | None
    detail: str = ""


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        env={**os.environ},
    )


def environment_status(manifest: Manifest) -> str:
    """``"n/a"`` (no dependencies), ``"ok"`` (installed and current), ``"missing"`` (declared but
    never installed), or ``"stale"`` (installed against a different dependency table, or the
    recorded interpreter is gone).

    This is the same computation :meth:`Manifest.resolved_interpreter` refuses to spawn on: any
    value other than ``n/a`` / ``ok`` for a dependency-declaring brick means the kernel would
    otherwise be running that brick's code against the wrong packages.
    """
    return manifest.environment_state()


def resolve_environment(
    manifest: Manifest,
    *,
    repo_root: Path | None = None,
    with_sdk: bool = True,
    force: bool = False,
) -> EnvResult:
    """Resolve ``manifest``'s private environment, writing ``<brick>/.veridian/environment.json``.

    ``with_sdk`` also installs the kernel package into a python venv so ``import veridian.sdk``
    works there (real reference bricks need it; dependency-free fixtures pass ``with_sdk=False``).
    ``force`` rebuilds even when the fingerprint already matches.
    """
    if not manifest.needs_isolated_env():
        return EnvResult(manifest.name, manifest.runtime, "skipped", None, "no [dependencies] table")

    if not force and environment_status(manifest) == "ok":
        record = manifest.load_env_record() or {}
        return EnvResult(
            manifest.name, record.get("runtime", manifest.runtime), "reused", record.get("interpreter")
        )

    deps = manifest.dependencies
    env_dir = manifest.directory / ENV_DIRNAME
    env_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = dependency_fingerprint(deps)

    if deps.get("python") or deps.get("python_version"):
        interpreter = _resolve_python_env(manifest, env_dir, repo_root, with_sdk)
        runtime = "python"
    elif deps.get("node"):
        interpreter = None
        _resolve_node_env(manifest, deps["node"])
        runtime = "node"
    else:  # pragma: no cover - guarded by needs_isolated_env
        return EnvResult(manifest.name, manifest.runtime, "skipped", None, "empty [dependencies]")

    record = {
        "brick": manifest.name,
        "runtime": runtime,
        "fingerprint": fingerprint,
        "interpreter": interpreter,
        "dependencies": deps,
        "resolved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (env_dir / ENV_RECORD_FILENAME).write_text(json.dumps(record, indent=2), encoding="utf-8")
    return EnvResult(manifest.name, runtime, "created", interpreter)


def _resolve_python_env(
    manifest: Manifest, env_dir: Path, repo_root: Path | None, with_sdk: bool
) -> str:
    uv = shutil.which("uv")
    if not uv:
        raise EnvironmentError_("`uv` is not on PATH; cannot resolve a python brick environment")

    venv_dir = env_dir / "venv"
    if venv_dir.exists():
        shutil.rmtree(venv_dir, ignore_errors=True)

    mk = [uv, "venv", str(venv_dir)]
    if manifest.dependencies.get("python_version"):
        mk += ["--python", manifest.dependencies["python_version"]]
    proc = _run(mk)
    if proc.returncode != 0:
        raise EnvironmentError_(f"uv venv failed for {manifest.name}:\n{proc.stderr.strip()}")

    python = _venv_python(venv_dir)
    targets: list[str] = []
    if with_sdk:
        from veridian.kernel.config import find_repo_root

        root = repo_root or find_repo_root(manifest.directory)
        targets.append("--editable")
        targets.append(str(root))
    targets += list(manifest.dependencies.get("python", []))

    if targets:
        install = [uv, "pip", "install", "--python", str(python), *targets]
        proc = _run(install)
        if proc.returncode != 0:
            raise EnvironmentError_(
                f"uv pip install failed for {manifest.name}:\n{proc.stderr.strip()}"
            )
    return str(python)


def _resolve_node_env(manifest: Manifest, node_deps: dict[str, str]) -> None:
    npm = shutil.which("npm")
    if not npm:
        raise EnvironmentError_("`npm` is not on PATH; cannot resolve a node brick environment")

    pkg_path = manifest.directory / "package.json"
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pkg = {"name": manifest.name.replace("/", "-"), "private": True}
    pkg.setdefault("dependencies", {})
    pkg["dependencies"].update(node_deps)
    pkg_path.write_text(json.dumps(pkg, indent=2), encoding="utf-8")

    proc = _run([npm, "install", "--prefix", str(manifest.directory)], cwd=manifest.directory)
    if proc.returncode != 0:
        raise EnvironmentError_(f"npm install failed for {manifest.name}:\n{proc.stderr.strip()}")
