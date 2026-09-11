"""Acquisition: obtain a brick or a stack from somewhere and install it under ``VERIDIAN_HOME``.

``veridian brick install`` (Milestone 1) only ever built a dependency environment for a brick
*already on disk*. This module is the missing half: getting the brick onto disk in the first
place, from

* a **local path** — a directory containing a ``veridian.toml`` (or, for a stack, a ``.toml``
  file),
* a **git URL** — cloned shallow; the resolved commit is recorded,
* an **archive URL** — ``.zip`` / ``.tar.gz`` / ``.tgz`` / ``.tar`` downloaded and unpacked.

Everything is **copied**, never symlinked (Windows host). A brick lands at
``VERIDIAN_HOME/bricks/<name>/<version>/`` so multiple versions coexist; a stack lands at
``VERIDIAN_HOME/stacks/<name>.toml``. Beside each install a provenance record
(``.veridian/install.json`` for a brick, ``.veridian/<name>.install.json`` for a stack) captures
the source, the git commit where applicable, a content hash, and the install timestamp. That is
*not* a signature — signing belongs to the marketplace — it is so a user can see where a brick
came from.

After a brick is copied, its dependency environment is resolved through the existing
:mod:`veridian.plugin_runtime.environments` path, not a reimplementation of it.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from veridian import __version__
from veridian.plugin_runtime.environments import EnvResult, resolve_environment
from veridian.plugin_runtime.home import home_bricks, home_stacks
from veridian.plugin_runtime.manifest import MANIFEST_FILENAME, Manifest, load_manifest

_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz", ".tar", ".tar.bz2")
_COPY_IGNORE = shutil.ignore_patterns(".git", ".veridian", "node_modules", "__pycache__", ".pytest_cache")
INSTALL_RECORD = "install.json"


class AcquireError(RuntimeError):
    """A source could not be obtained or installed."""


class ConsentDeclined(AcquireError):
    """The user declined the dependency-install consent prompt."""


class AlreadyInstalled(AcquireError):
    """The target version is already installed and ``force`` was not given."""


# -- source classification ---------------------------------------------------------


def classify_source(source: str) -> str:
    """``"local"`` | ``"git"`` | ``"archive"``."""
    s = source.strip()
    if s.startswith(("git+", "git@")) or s.endswith(".git"):
        return "git"
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https"):
        path = parsed.path.lower()
        if any(path.endswith(suf) for suf in _ARCHIVE_SUFFIXES):
            return "archive"
        if parsed.netloc in ("github.com", "gitlab.com", "bitbucket.org"):
            return "git"
        # A bare https URL with no archive suffix: assume it points at a git repo.
        return "git"
    if parsed.scheme in ("ssh",):
        return "git"
    return "local"


# -- fetch into a working directory ----------------------------------------------


@dataclass
class _Fetched:
    root: Path  # directory that contains (somewhere) a veridian.toml
    source: str
    kind: str
    git_commit: str | None = None


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True)


def _fetch_git(source: str, workdir: Path, ref: str | None) -> _Fetched:
    if not shutil.which("git"):
        raise AcquireError("`git` is not on PATH; cannot clone a git source")
    url = source[len("git+") :] if source.startswith("git+") else source
    if "#" in url and ref is None:
        url, _, ref = url.partition("#")
    dest = workdir / "clone"
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest)]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise AcquireError(f"git clone failed:\n{proc.stderr.strip()}")
    head = _run(["git", "rev-parse", "HEAD"], cwd=dest)
    commit = head.stdout.strip() or None
    return _Fetched(root=dest, source=source, kind="git", git_commit=commit)


def _fetch_archive(source: str, workdir: Path) -> _Fetched:
    try:
        import httpx
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise AcquireError("httpx is required to fetch an archive URL") from exc
    try:
        resp = httpx.get(source, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise AcquireError(f"could not download {source}: {exc}") from exc

    dest = workdir / "unpacked"
    dest.mkdir(parents=True, exist_ok=True)
    lower = urlparse(source).path.lower()
    data = resp.content
    if lower.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            _safe_extract_zip(zf, dest)
    else:
        mode = "r:*"
        with tarfile.open(fileobj=io.BytesIO(data), mode=mode) as tf:
            _safe_extract_tar(tf, dest)
    return _Fetched(root=dest, source=source, kind="archive")


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    for member in zf.namelist():
        target = (dest / member).resolve()
        if not str(target).startswith(str(dest.resolve())):
            raise AcquireError(f"archive entry escapes extraction dir: {member}")
    zf.extractall(dest)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    base = dest.resolve()
    for member in tf.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(base)):
            raise AcquireError(f"archive entry escapes extraction dir: {member.name}")
    tf.extractall(dest)


def _fetch_local(source: str) -> _Fetched:
    p = Path(source).expanduser().resolve()
    if not p.exists():
        raise AcquireError(f"local source does not exist: {p}")
    return _Fetched(root=p, source=str(p), kind="local")


def _fetch(source: str, workdir: Path, ref: str | None) -> _Fetched:
    kind = classify_source(source)
    if kind == "git":
        return _fetch_git(source, workdir, ref)
    if kind == "archive":
        return _fetch_archive(source, workdir)
    return _fetch_local(source)


def _find_manifest_dir(root: Path, *, wanted: str = MANIFEST_FILENAME) -> Path:
    """The directory holding ``veridian.toml`` — at ``root`` or one level below it (archive
    tarballs and clones often wrap the brick in a single top folder)."""
    if (root / wanted).is_file():
        return root
    if root.is_dir():
        subdirs = [c for c in sorted(root.iterdir()) if c.is_dir()]
        hits = [d for d in subdirs if (d / wanted).is_file()]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise AcquireError(
                f"source contains multiple bricks ({', '.join(d.name for d in hits)}); "
                f"point at one of them"
            )
        if len(subdirs) == 1 and (subdirs[0] / wanted).is_file():
            return subdirs[0]
    raise AcquireError(f"no {wanted} found in {root}")


# -- provenance ------------------------------------------------------------------


def content_hash(directory: Path) -> str:
    """A stable ``sha256:...`` digest of every file under ``directory`` except the ``.veridian``
    bookkeeping dir. Order-independent: paths are sorted, and each contributes its relative path
    and bytes."""
    h = hashlib.sha256()
    root = directory.resolve()
    files = sorted(
        p for p in root.rglob("*")
        if p.is_file() and ".veridian" not in p.relative_to(root).parts
    )
    for p in files:
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


@dataclass(frozen=True)
class InstallRecord:
    name: str
    version: str
    source: str
    source_kind: str
    installed_at: str
    content_hash: str
    veridian_version: str
    git_commit: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "version": self.version,
                "source": self.source,
                "source_kind": self.source_kind,
                "git_commit": self.git_commit,
                "installed_at": self.installed_at,
                "content_hash": self.content_hash,
                "veridian_version": self.veridian_version,
            },
            indent=2,
        )

    @classmethod
    def from_dir(cls, brick_dir: Path) -> "InstallRecord | None":
        rec_path = brick_dir / ".veridian" / INSTALL_RECORD
        try:
            d = json.loads(rec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return cls(
            name=d.get("name", "?"),
            version=d.get("version", "?"),
            source=d.get("source", "?"),
            source_kind=d.get("source_kind", "?"),
            installed_at=d.get("installed_at", "?"),
            content_hash=d.get("content_hash", "?"),
            veridian_version=d.get("veridian_version", "?"),
            git_commit=d.get("git_commit"),
        )


def _write_record(brick_dir: Path, rec: InstallRecord) -> None:
    meta = brick_dir / ".veridian"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / INSTALL_RECORD).write_text(rec.to_json(), encoding="utf-8")


# -- brick add / remove --------------------------------------------------------


@dataclass
class BrickInstall:
    name: str
    version: str
    path: Path
    record: InstallRecord
    env: EnvResult | None = None
    consent_prompted: bool = False


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def add_brick(
    source: str,
    *,
    ref: str | None = None,
    force: bool = False,
    yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    resolve_env: bool = True,
    with_sdk: bool = True,
    home: Path | None = None,
) -> BrickInstall:
    """Acquire the brick at ``source`` and install it under ``VERIDIAN_HOME/bricks``.

    ``confirm`` is called with a warning string when the brick declares a ``[dependencies]`` table
    (installing it will run ``uv pip install`` / ``npm install``, which execute code from the
    brick's own dependency tree). It must return ``True`` to proceed; ``yes=True`` skips the
    prompt. ``home`` overrides ``VERIDIAN_HOME`` for tests.
    """
    dest_root = (home / "bricks") if home else home_bricks()
    with tempfile.TemporaryDirectory(prefix="veridian-acquire-") as tmp:
        fetched = _fetch(source, Path(tmp), ref)
        brick_src = _find_manifest_dir(fetched.root)
        manifest = load_manifest(brick_src)

        dest = dest_root / manifest.name / manifest.version
        if dest.exists():
            if not force:
                raise AlreadyInstalled(
                    f"{manifest.name}@{manifest.version} is already installed at {dest}; "
                    f"pass --force to reinstall or `veridian brick remove {manifest.name}@{manifest.version}`"
                )
            shutil.rmtree(dest)

        needs_env = manifest.needs_isolated_env()
        consent_prompted = False
        if needs_env and resolve_env and not yes:
            consent_prompted = True
            warning = (
                f"Installing {manifest.name}@{manifest.version} will run "
                f"{'`npm install`' if manifest.dependencies.get('node') else '`uv pip install`'} "
                f"for its declared dependencies {_dep_summary(manifest)}.\n"
                f"That executes setup code from those packages on your machine. Continue?"
            )
            approver = confirm or (lambda _msg: False)
            if not approver(warning):
                raise ConsentDeclined(
                    f"declined: {manifest.name}@{manifest.version} not installed "
                    f"(re-run with --yes to install non-interactively)"
                )

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(brick_src, dest, ignore=_COPY_IGNORE)

        record = InstallRecord(
            name=manifest.name,
            version=manifest.version,
            source=fetched.source,
            source_kind=fetched.kind,
            git_commit=fetched.git_commit,
            installed_at=_now(),
            content_hash=content_hash(dest),
            veridian_version=__version__,
        )
        _write_record(dest, record)

        env_result: EnvResult | None = None
        if needs_env and resolve_env:
            installed_manifest = load_manifest(dest)
            env_result = resolve_environment(installed_manifest, with_sdk=with_sdk)

    return BrickInstall(
        name=manifest.name,
        version=manifest.version,
        path=dest,
        record=record,
        env=env_result,
        consent_prompted=consent_prompted,
    )


def _dep_summary(manifest: Manifest) -> str:
    deps = manifest.dependencies
    parts: list[str] = []
    if deps.get("python"):
        parts.append(f"python: {', '.join(deps['python'])}")
    if deps.get("node"):
        parts.append(f"node: {', '.join(deps['node'])}")
    return "(" + "; ".join(parts) + ")" if parts else ""


def remove_brick(name: str, version: str | None = None, *, home: Path | None = None) -> list[Path]:
    """Delete ``name`` (all versions) or ``name@version`` from ``VERIDIAN_HOME/bricks``.
    Returns the directories removed. Prunes now-empty parent directories."""
    dest_root = (home / "bricks") if home else home_bricks()
    brick_root = dest_root / name
    if not brick_root.exists():
        raise AcquireError(f"{name} is not installed under {dest_root}")

    removed: list[Path] = []
    if version:
        target = brick_root / version
        if not target.exists():
            available = ", ".join(sorted(p.name for p in brick_root.iterdir() if p.is_dir())) or "none"
            raise AcquireError(f"{name} has no installed version {version!r} (installed: {available})")
        shutil.rmtree(target)
        removed.append(target)
        if not any(brick_root.iterdir()):
            brick_root.rmdir()
    else:
        shutil.rmtree(brick_root)
        removed.append(brick_root)

    # prune empty namespace parents (e.g. .../bricks/context/ once nothing is left)
    parent = brick_root.parent
    while parent != dest_root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    return removed


def list_installed_bricks(*, home: Path | None = None) -> list[tuple[Manifest, InstallRecord | None]]:
    dest_root = (home / "bricks") if home else home_bricks()
    out: list[tuple[Manifest, InstallRecord | None]] = []
    if not dest_root.is_dir():
        return out
    from veridian.plugin_runtime.loader import _brick_dirs  # noqa: PLC0415

    for d in _brick_dirs(dest_root):
        try:
            m = load_manifest(d)
        except Exception:  # noqa: BLE001
            continue
        out.append((m, InstallRecord.from_dir(d)))
    return out


# -- stack add ------------------------------------------------------------------


@dataclass(frozen=True)
class StackInstall:
    name: str
    path: Path
    record: InstallRecord


def add_stack(
    source: str,
    *,
    ref: str | None = None,
    path_in_repo: str | None = None,
    force: bool = False,
    home: Path | None = None,
) -> StackInstall:
    """Acquire a stack ``.toml`` from ``source`` and install it under ``VERIDIAN_HOME/stacks``.

    ``source`` may be a local ``.toml`` file, an archive URL, or a git URL. For a git/archive
    source the stack file is taken from ``path_in_repo`` if given, else a single top-level
    ``*.toml`` (``stack.toml`` / ``veridian.stack.toml`` preferred)."""
    import tomllib

    dest_root = (home / "stacks") if home else home_stacks()
    with tempfile.TemporaryDirectory(prefix="veridian-acquire-") as tmp:
        fetched = _fetch(source, Path(tmp), ref)
        if fetched.kind == "local" and fetched.root.is_file():
            stack_file = fetched.root
        else:
            stack_file = _find_stack_file(fetched.root, path_in_repo)

        try:
            raw = tomllib.loads(stack_file.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise AcquireError(f"{stack_file}: not a readable TOML stack file: {exc}") from exc
        name = raw.get("stack", {}).get("name") or stack_file.stem
        if not isinstance(name, str) or not name:
            raise AcquireError(f"{stack_file}: [stack].name is missing")

        dest = dest_root / f"{name}.toml"
        if dest.exists() and not force:
            raise AlreadyInstalled(
                f"a stack named {name!r} is already installed at {dest}; pass --force to overwrite"
            )
        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(stack_file, dest)

        record = InstallRecord(
            name=name,
            version=raw.get("stack", {}).get("version", "-"),
            source=fetched.source,
            source_kind=fetched.kind,
            git_commit=fetched.git_commit,
            installed_at=_now(),
            content_hash="sha256:" + hashlib.sha256(dest.read_bytes()).hexdigest(),
            veridian_version=__version__,
        )
        meta = dest_root / ".veridian"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / f"{name}.install.json").write_text(record.to_json(), encoding="utf-8")

    return StackInstall(name=name, path=dest, record=record)


def _find_stack_file(root: Path, path_in_repo: str | None) -> Path:
    if path_in_repo:
        cand = root / path_in_repo
        if cand.is_file():
            return cand
        raise AcquireError(f"{path_in_repo} not found in source")
    # look at root, then one level down
    search_dirs = [root]
    if root.is_dir():
        subs = [c for c in sorted(root.iterdir()) if c.is_dir()]
        if len(subs) == 1:
            search_dirs.append(subs[0])
    preferred = ("veridian.stack.toml", "stack.toml")
    for d in search_dirs:
        for name in preferred:
            if (d / name).is_file():
                return d / name
        tomls = sorted(d.glob("*.toml"))
        if len(tomls) == 1:
            return tomls[0]
    raise AcquireError(f"no stack .toml found in {root}; pass --path")


def stack_install_record(name: str, *, home: Path | None = None) -> InstallRecord | None:
    dest_root = (home / "stacks") if home else home_stacks()
    rec_path = dest_root / ".veridian" / f"{name}.install.json"
    try:
        d = json.loads(rec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return InstallRecord(
        name=d.get("name", name),
        version=d.get("version", "-"),
        source=d.get("source", "?"),
        source_kind=d.get("source_kind", "?"),
        installed_at=d.get("installed_at", "?"),
        content_hash=d.get("content_hash", "?"),
        veridian_version=d.get("veridian_version", "?"),
        git_commit=d.get("git_commit"),
    )
