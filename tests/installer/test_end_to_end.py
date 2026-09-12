"""Install Veridian the way a stranger would, then run it from somewhere else entirely.

Marked ``installer`` and deselected by default: each run builds a real virtual environment. It is
the only automated check that the two claims the installer exists to make still hold — that a
released tarball installs without a checkout, and that the directory you run the result from
becomes the workspace.

The ``--tarball`` / ``-Tarball`` flag is what lets this run against a locally built archive, so
nothing has to be published first.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from veridian.kernel.config import find_repo_root

pytestmark = pytest.mark.installer

REPO = find_repo_root()

# Everything the runtime locates as siblings under one root. A tarball missing any of these
# installs fine and then fails at the first stack load.
REQUIRED = ("pyproject.toml", "src", "bricks", "stacks", "schemas", "pre-installed", "sdks")
SKIP = {".git", ".venv", "tests", ".github", "examples", "__pycache__", ".pytest_cache", ".veridian"}


@pytest.fixture(scope="module")
def tarball(tmp_path_factory) -> Path:
    """A release-shaped archive of the working tree: one top-level directory, no dev cruft."""
    out = tmp_path_factory.mktemp("dist") / "veridian-test.tar.gz"

    def keep(item: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(item.name).parts
        return None if any(p in SKIP for p in parts) else item

    with tarfile.open(out, "w:gz") as tar:
        tar.add(REPO, arcname="veridian-test", filter=keep)
    return out


def short_root(tmp_path_factory) -> Path:
    """Windows still caps most paths at 260 characters, and a venv nests deeply enough that
    pytest's own temp directory can overflow it. Install somewhere shallow instead."""
    base = Path(os.environ.get("TEMP", "/tmp")) / "veridian-installer-test"
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    (base / "tmp").mkdir(parents=True)
    return base


@pytest.fixture(scope="module")
def installed(tarball, tmp_path_factory):
    base = short_root(tmp_path_factory)
    home = base / "home"
    env = {**os.environ, "VERIDIAN_HOME": str(home), "TMPDIR": str(base / "tmp")}

    if sys.platform == "win32":
        cmd = [
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(REPO / "install.ps1"),
            "-Tarball", str(tarball), "-NoModifyPath",
        ]
        launcher = home / "bin" / "veridian.exe"
    else:
        cmd = ["sh", str(REPO / "install.sh"), "--tarball", str(tarball), "--no-modify-path"]
        launcher = home / "bin" / "veridian"

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert result.returncode == 0, f"installer failed:\n{result.stdout}\n{result.stderr}"
    assert launcher.exists(), f"no launcher at {launcher}"

    yield launcher, home, base
    shutil.rmtree(base, ignore_errors=True)


def run(launcher: Path, *args: str, cwd: Path, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "VERIDIAN_HOME": str(home), "VERIDIAN_NO_ONBOARDING": "1"}
    return subprocess.run(
        [str(launcher), *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300
    )


def test_tarball_carries_every_directory_the_runtime_resolves(tarball):
    with tarfile.open(tarball) as tar:
        top = {Path(n).parts[1] for n in tar.getnames() if len(Path(n).parts) > 1}
    assert set(REQUIRED) <= top, f"missing from the archive: {set(REQUIRED) - top}"


def test_runs_from_an_unrelated_directory(installed):
    """The whole point: no checkout anywhere above the current directory."""
    launcher, home, base = installed
    project = base / "someones-project"
    project.mkdir(exist_ok=True)

    result = run(launcher, "--version", cwd=project, home=home)
    assert result.returncode == 0, result.stderr
    assert "(installed)" in result.stdout


def test_doctor_passes_and_finds_the_builtin_bricks(installed):
    launcher, home, base = installed
    project = base / "someones-project"
    project.mkdir(exist_ok=True)

    result = run(launcher, "doctor", cwd=project, home=home)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "bricks discoverable" in result.stdout
    assert "0 bricks" not in result.stdout


def test_the_working_directory_becomes_the_workspace(installed):
    launcher, home, base = installed
    project = base / "workspace-check"
    project.mkdir(exist_ok=True)

    python = next(home.glob("versions/*/venv/**/python.exe"), None) or next(
        home.glob("versions/*/venv/bin/python")
    )
    probe = (
        "from pathlib import Path;"
        "from veridian.kernel import Kernel, load_stack;"
        "from veridian.cli._common import default_stack;"
        "k = Kernel(load_stack(default_stack()));"
        "print(k.workspace_root == Path.cwd().resolve())"
    )
    result = subprocess.run(
        [str(python), "-c", probe],
        cwd=project,
        env={**os.environ, "VERIDIAN_HOME": str(home)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("True")


def test_reinstalling_the_same_version_is_a_no_op(installed, tarball):
    launcher, home, base = installed
    env = {**os.environ, "VERIDIAN_HOME": str(home), "TMPDIR": str(base / "tmp")}
    if sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-File", str(REPO / "install.ps1"), "-Tarball", str(tarball), "-NoModifyPath"]
    else:
        cmd = ["sh", str(REPO / "install.sh"), "--tarball", str(tarball), "--no-modify-path"]

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    assert result.returncode == 0, result.stderr
    assert "already installed" in result.stdout
    assert run(launcher, "--version", cwd=base, home=home).returncode == 0
