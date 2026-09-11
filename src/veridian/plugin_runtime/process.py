"""Brick subprocess supervision.

This is where crash isolation is won or lost, and where Windows differs. There are no POSIX
signals: shutdown is ``Popen.terminate()`` (``TerminateProcess`` on Windows) followed by a hard
``kill()`` if the process has not exited within ``kill_timeout`` seconds.

``stdout`` is protocol and is handed to a :class:`StdioTransport`. ``stderr`` is the brick's log
stream: it is drained line by line and each line is passed to ``on_stderr`` (the kernel forwards it
to the event bus). A brick writing garbage to ``stdout`` is the codec's problem, not this module's.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from veridian.plugin_runtime.ipc import Endpoint
from veridian.plugin_runtime.manifest import IsolationSpec, Manifest
from veridian.plugin_runtime.transport import StdioTransport

StderrSink = Callable[[str], None]
MalformedSink = Callable[[str], None]

_DEFAULT_KILL_TIMEOUT = 5.0


def _detached_spawn_kwargs() -> dict[str, object]:
    """Spawn kwargs that put a brick in its own process / signal group, so a Ctrl-C (or SIGINT)
    aimed at the kernel's console is **not** also delivered straight to every brick.

    This matters most on Windows. There, a console ``CTRL_C_EVENT`` goes to *every* process
    attached to the console at once; a brick that gets it unwinds its ``asyncio.run`` and the
    interpreter begins finalising while the SDK's daemon stdin-reader thread is still parked in a
    blocking read — which finalisation races into a ``0xC0000005`` access violation. With
    ``CREATE_NEW_PROCESS_GROUP`` the signal reaches only the kernel, which then shuts each brick
    down in order (``plugin.shutdown`` + closing its stdin, which is what actually unblocks that
    reader thread). POSIX gets the equivalent via ``start_new_session`` so the behaviour — bricks
    are insulated from the foreground Ctrl-C and stopped only by the kernel — is the same on both.
    """
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP (0x00000200): brick no longer receives the console's Ctrl-C.
        return {"creationflags": 0x00000200}
    return {"start_new_session": True}

# Teardown must never be able to wedge the kernel. Every container-engine invocation on the
# shutdown path is time-boxed, and the removal of a proxy's networks is ordered (container first,
# then its networks) and retried: a just-removed ``--rm`` brick container can still hold an
# endpoint on an ``--internal`` network for a beat, and ``network rm`` fails until it lets go.
_ENGINE_CMD_TIMEOUT = 15.0
_TEARDOWN_ATTEMPTS = 6
_TEARDOWN_BACKOFF = 0.5
_TEARDOWN_BACKOFF_CAP = 2.0
# Hard ceiling on the whole post_run sequence, whatever the engine does. Past this, remaining
# commands are still *attempted once* (so a leak is unlikely) but no longer retried or waited on.
_TEARDOWN_TOTAL_BUDGET = 45.0
# stderr fragments that mean "the thing you asked me to remove is already gone" — a success for us.
_RESOURCE_ABSENT_MARKERS = ("no such", "not found")


class BrickProcess:
    def __init__(
        self,
        name: str,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        kill_timeout: float = _DEFAULT_KILL_TIMEOUT,
        on_stderr: StderrSink | None = None,
        on_malformed: MalformedSink | None = None,
        on_exit: Callable[[int], None] | None = None,
        pre_run: tuple[tuple[str, ...], ...] = (),
        post_run: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        self.name = name
        self.command = command
        self.cwd = Path(cwd)
        self.env = env
        self.kill_timeout = kill_timeout
        self._on_stderr = on_stderr
        self._on_malformed = on_malformed
        self._on_exit = on_exit
        self._pre_run = pre_run
        self._post_run = post_run
        self._stopping = False

        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._exit_task: asyncio.Task[None] | None = None
        self._endpoint: Endpoint | None = None
        self._exited = asyncio.Event()
        # Serialises teardown so the exit-watcher and an explicit stop() cannot half-run it
        # between them. Whoever gets here second still waits on the lock until the first has
        # finished, so stop() never returns while networks are still being torn down.
        self._post_run_lock = asyncio.Lock()

    async def start(self) -> Endpoint:
        if self._proc is not None:
            raise RuntimeError(f"brick {self.name} already started")
        self.cwd.mkdir(parents=True, exist_ok=True)
        try:
            for cmd in self._pre_run:
                await _run_engine_command(cmd, check=True)
        except Exception:
            await self._run_post_run()  # roll back anything the earlier pre_run commands created
            raise
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
            env=self.env,
            **_detached_spawn_kwargs(),
        )
        assert self._proc.stdin and self._proc.stdout and self._proc.stderr
        transport = StdioTransport(self._proc.stdout, self._proc.stdin)
        self._endpoint = Endpoint(transport, name=self.name, on_malformed=self._on_malformed)
        self._endpoint.start()
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(self._proc.stderr), name=f"stderr:{self.name}"
        )
        self._exit_task = asyncio.create_task(self._watch_exit(), name=f"exit-watch:{self.name}")
        return self._endpoint

    async def _watch_exit(self) -> None:
        assert self._proc is not None
        code = await self._proc.wait()
        self._exited.set()
        # The brick process is gone; drop any egress proxy / networks it stood up. Best-effort and
        # idempotent, so a later stop() re-running these is harmless.
        await self._run_post_run()
        if self._on_exit is not None and not self._stopping:
            self._on_exit(code)

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        try:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                if self._on_stderr:
                    self._on_stderr(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        except (asyncio.CancelledError, ConnectionResetError):
            pass

    @property
    def endpoint(self) -> Endpoint:
        if self._endpoint is None:
            raise RuntimeError(f"brick {self.name} not started")
        return self._endpoint

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc else None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def wait(self) -> int:
        await self._exited.wait()
        assert self._proc is not None and self._proc.returncode is not None
        return self._proc.returncode

    async def stop(self) -> int:
        """Terminate, then hard-kill after ``kill_timeout``. Returns the exit code. Idempotent."""
        self._stopping = True
        if self._proc is None:
            return 0
        if self._proc.returncode is not None:
            await self._cleanup()
            return self._proc.returncode

        try:
            self._proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), self.kill_timeout)
        except asyncio.TimeoutError:
            try:
                self._proc.kill()
            except ProcessLookupError:
                pass
            await self._proc.wait()
        await self._cleanup()
        return self._proc.returncode if self._proc.returncode is not None else -1

    async def _cleanup(self) -> None:
        if self._endpoint is not None:
            await self._endpoint.aclose()
        if self._stderr_task is not None:
            self._stderr_task.cancel()
        self._exited.set()
        await self._run_post_run()
        # If the exit-watcher raced us to teardown, let it unwind now rather than leaving it
        # pending on a loop that is about to close (that is what used to hang shutdown).
        if self._exit_task is not None and not self._exit_task.done():
            try:
                await asyncio.wait_for(self._exit_task, _ENGINE_CMD_TIMEOUT)
            except (asyncio.CancelledError, Exception):
                pass

    async def _run_post_run(self) -> None:
        """Run the teardown commands once, to completion, tolerating any failure.

        Ordered and retried: the proxy container is removed first, then the networks it sat on,
        and each command is retried while it keeps failing transiently (a just-removed ``--rm``
        brick container can still hold an endpoint on an ``--internal`` network for a beat, and
        ``docker network rm`` reports "has active endpoints" until it lets go). Serialised by a
        lock so the exit-watcher and an explicit ``stop()`` cannot half-run it between them; the
        second caller finds nothing left in ``_post_run`` but still blocks here until the first
        has finished. Every engine call is time-boxed, so an unhealthy proxy or a wedged daemon
        cannot stall shutdown."""
        async with self._post_run_lock:
            if not self._post_run:
                return
            pending, self._post_run = self._post_run, ()
            deadline = asyncio.get_running_loop().time() + _TEARDOWN_TOTAL_BUDGET
            for cmd in pending:
                await _run_teardown_command(cmd, deadline=deadline)


def base_env(passthrough: list[str], extra: dict[str, str] | None = None) -> dict[str, str]:
    """A scrubbed environment: nothing from the parent except an explicit allowlist, plus the
    variables a brick always needs to run at all, plus kernel-injected extras.

    On Windows a child process cannot start without ``SystemRoot`` (and friends); those, plus the
    interpreter's own path variables, are always included. The home-directory variables are here
    too: HTTP client libraries (``httpx`` and the provider SDKs built on it) resolve ``Path.home()``
    at construction — for the TLS trust store, ``.netrc``, and their own config — and raise on a
    machine with none set. They are a path, not a secret. Everything else must be named in the
    brick manifest's ``env_passthrough``.
    """
    always = [
        "SYSTEMROOT", "SYSTEMDRIVE", "PATH", "PATHEXT", "TEMP", "TMP", "WINDIR", "COMSPEC",
        "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
        # So a brick resolves the same VERIDIAN_HOME as the kernel — it writes its crash log
        # there. A path, not a secret (same rationale as the home vars above).
        "VERIDIAN_HOME",
    ]
    env: dict[str, str] = {}
    for key in [*always, *passthrough]:
        for variant in {key, key.upper(), key.lower()}:
            if variant in os.environ:
                env[variant] = os.environ[variant]
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def python_command(script: Path) -> list[str]:
    """Spawn command for a Python brick that runs under the same interpreter as the kernel."""
    return [sys.executable, str(script)]


class EngineCommandError(RuntimeError):
    """A container-engine command run as a brick's ``pre_run`` failed."""


async def _engine_command(
    cmd: tuple[str, ...], *, timeout: float | None = _ENGINE_CMD_TIMEOUT
) -> tuple[int, str]:
    """Run one container-engine command to completion. Returns ``(exit_code, stderr_text)``;
    never raises for the command's own failure — callers decide what a non-zero code means.

    With ``timeout`` set the child is killed and ``124`` returned if it overruns — the teardown
    path relies on this so a wedged daemon or an unhealthy proxy can't stall shutdown. ``pre_run``
    passes ``None``: standing up a proxy may legitimately pull an image first."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=base_env(_ENGINE_ENV_PASSTHROUGH),
        )
    except OSError as exc:  # engine binary vanished mid-run, PATH race, &c.
        return 1, str(exc)
    if timeout is None:
        _out, err = await proc.communicate()
        return (proc.returncode or 0), err.decode("utf-8", "replace")
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.communicate(), 5.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass
        return 124, f"`{' '.join(cmd)}` timed out after {timeout}s"
    return (proc.returncode or 0), err.decode("utf-8", "replace")


async def _run_engine_command(cmd: tuple[str, ...], *, check: bool) -> int:
    """Run one container-engine command (a brick's ``pre_run``). With ``check`` a non-zero exit
    raises :class:`EngineCommandError`; without it the exit code is just returned."""
    rc, err = await _engine_command(cmd, timeout=None)
    if check and rc != 0:
        raise EngineCommandError(f"`{' '.join(cmd)}` exited {rc}: {err.strip()}")
    return rc


async def _run_teardown_command(cmd: tuple[str, ...], *, deadline: float | None = None) -> None:
    """Run one teardown command, retrying while it fails transiently. Removing a network the
    brick's ``--rm`` container has not finished detaching from fails with "has active
    endpoints"; a short bounded retry rides that out. "Already gone" counts as done. ``deadline``
    (a loop clock value) caps the retrying — once it passes, the command has already been tried
    at least once and we move on rather than let shutdown drag."""
    loop = asyncio.get_running_loop()
    delay = _TEARDOWN_BACKOFF
    err = ""
    for attempt in range(1, _TEARDOWN_ATTEMPTS + 1):
        rc, err = await _engine_command(cmd)
        if rc == 0 or any(marker in err.lower() for marker in _RESOURCE_ABSENT_MARKERS):
            return
        if attempt >= _TEARDOWN_ATTEMPTS or (deadline is not None and loop.time() >= deadline):
            break
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, _TEARDOWN_BACKOFF_CAP)
    sys.stderr.write(
        f"veridian: teardown command did not succeed: `{' '.join(cmd)}`: {err.strip()}\n"
    )


# ---------------------------------------------------------------------------------------------------
# Spawn strategies
#
# Milestone 1 had exactly one way to start a brick: a plain subprocess (``ProcessSpawn``). Milestone
# 2 adds ``ContainerSpawn``, which wraps the same brick command in ``docker run`` / ``podman run`` so
# ``isolation.mode = "container"`` is enforced by the kernel for *any* brick, not only for code the
# sandbox brick runs. ``BrickProcess`` is unchanged — a strategy only decides the argv, env, and cwd
# handed to it. The strategy for a manifest is chosen by :func:`spawn_strategy_for`.
# ---------------------------------------------------------------------------------------------------

CONTAINER_WORKSPACE = "/workspace"
CONTAINER_BRICK = "/brick"

# Host-only variables that mean nothing inside a Linux container and must not be forwarded as the
# brick's environment (the container image supplies its own).
_HOST_ONLY_ENV = {"SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP"}

# Variables the container engine CLI itself may need on the host.
_ENGINE_ENV_PASSTHROUGH = ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"]


class ContainerUnavailable(RuntimeError):
    """A brick declares ``isolation.mode = "container"`` but no usable container engine is on PATH."""


@dataclass(frozen=True)
class ResolvedSpawn:
    argv: list[str]
    env: dict[str, str]
    cwd: str
    # Engine commands the kernel runs immediately before / after the brick's own process. Used by
    # container mode to stand up and tear down a brick's egress proxy and its networks. ``post_run``
    # is best-effort: a failure is logged, never raised.
    pre_run: tuple[tuple[str, ...], ...] = ()
    post_run: tuple[tuple[str, ...], ...] = ()


class SpawnStrategy(ABC):
    @abstractmethod
    def resolve(
        self, manifest: Manifest, *, workspace_root: Path, brick_env: dict[str, str]
    ) -> ResolvedSpawn:
        """Return the argv / env / cwd to hand :class:`BrickProcess` for this brick."""


class ProcessSpawn(SpawnStrategy):
    """Milestone 1 behaviour: a plain subprocess, running the brick's resolved command directly in
    the workspace root with its scrubbed environment."""

    def resolve(
        self, manifest: Manifest, *, workspace_root: Path, brick_env: dict[str, str]
    ) -> ResolvedSpawn:
        return ResolvedSpawn(manifest.resolved_command(), dict(brick_env), str(workspace_root))


class ContainerSpawn(SpawnStrategy):
    """Run the brick inside a container the kernel drives.

    * the workspace is mounted read-write at ``/workspace`` and is the working directory;
    * the brick directory is mounted read-only at ``/brick``;
    * ``${python}`` / ``${node}`` resolve to the image's own interpreter (per-brick
      ``[dependencies]`` are *not* installed into the image — it must already carry what the brick
      needs), and brick-relative path arguments are rewritten under ``/brick``;
    * ``network = false`` (the default) is ``--network none`` — an OS-level egress deny.
    * ``network = true`` with a non-empty ``allow_hosts`` puts the brick on an ``--internal``
      network whose only exit is a sidecar :mod:`veridian.security.egress_proxy` that refuses every
      unlisted destination; ``HTTPS_PROXY`` is injected so the brick's HTTP client uses it. The
      proxy and its networks are created in ``pre_run`` and removed in ``post_run``.
    * ``network = true`` with no allowlist is unrestricted bridge networking (refused by the
      registry for any brick that also handles conversation content).
    """

    def __init__(self, spec: IsolationSpec, *, engine: str | None = None) -> None:
        self._spec = spec
        self._engine = engine or detect_container_engine(spec.engine)
        if self._engine is None:
            raise ContainerUnavailable(
                "isolation.mode = \"container\" but neither `docker` nor `podman` is on PATH"
            )

    @property
    def engine(self) -> str:
        assert self._engine is not None
        return self._engine

    def _container_command(self, manifest: Manifest) -> list[str]:
        out: list[str] = []
        for part in manifest.spawn_command:
            if part == "${python}":
                out.append("python")
            elif part == "${node}":
                out.append("node")
            elif not Path(part).is_absolute() and (manifest.directory / part).exists():
                out.append(f"{CONTAINER_BRICK}/{Path(part).as_posix()}")
            else:
                out.append(part)
        return out

    def resolve(
        self, manifest: Manifest, *, workspace_root: Path, brick_env: dict[str, str]
    ) -> ResolvedSpawn:
        assert self._spec.image, "container isolation requires an image (validated at parse time)"
        manifest.assert_egress_sane()
        from veridian.security.egress import plan_egress  # lazy: avoids a security<->runtime cycle

        egress = plan_egress(self._spec, engine=self.engine, image=self._spec.image)

        merged = {**brick_env, **egress.env}
        passed = {k: v for k, v in merged.items() if k.upper() not in _HOST_ONLY_ENV}
        env_args: list[str] = []
        for key, value in passed.items():
            env_args += ["-e", f"{key}={value}"]

        argv = [
            self.engine,
            "run",
            "--rm",
            "-i",
            *egress.network_args,
            "-v",
            f"{Path(workspace_root).as_posix()}:{CONTAINER_WORKSPACE}",
            "-v",
            f"{Path(manifest.directory).as_posix()}:{CONTAINER_BRICK}:ro",
            "-w",
            CONTAINER_WORKSPACE,
            *env_args,
            self._spec.image,
            *self._container_command(manifest),
        ]
        host_env = base_env(_ENGINE_ENV_PASSTHROUGH)
        return ResolvedSpawn(
            argv, host_env, str(workspace_root),
            pre_run=egress.pre_run, post_run=egress.post_run,
        )


def detect_container_engine(preferred: str | None = None) -> str | None:
    """Return the container engine to use: ``preferred`` if it is on PATH, else ``docker`` then
    ``podman``. ``None`` if nothing usable is installed."""
    candidates = [preferred] if preferred else ["docker", "podman"]
    for name in candidates:
        if name and shutil.which(name):
            return name
    return None


def spawn_strategy_for(manifest: Manifest) -> SpawnStrategy:
    """The spawn strategy a manifest asks for. ``isolation.mode`` defaults to ``"process"``."""
    if manifest.isolation.is_container:
        return ContainerSpawn(manifest.isolation)
    return ProcessSpawn()
