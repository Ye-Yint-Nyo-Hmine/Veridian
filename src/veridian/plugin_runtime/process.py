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
import sys
from collections.abc import Callable
from pathlib import Path

from veridian.plugin_runtime.ipc import Endpoint
from veridian.plugin_runtime.transport import StdioTransport

StderrSink = Callable[[str], None]
MalformedSink = Callable[[str], None]

_DEFAULT_KILL_TIMEOUT = 5.0


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
    ) -> None:
        self.name = name
        self.command = command
        self.cwd = Path(cwd)
        self.env = env
        self.kill_timeout = kill_timeout
        self._on_stderr = on_stderr
        self._on_malformed = on_malformed
        self._on_exit = on_exit
        self._stopping = False

        self._proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._endpoint: Endpoint | None = None
        self._exited = asyncio.Event()

    async def start(self) -> Endpoint:
        if self._proc is not None:
            raise RuntimeError(f"brick {self.name} already started")
        self.cwd.mkdir(parents=True, exist_ok=True)
        self._proc = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.cwd),
            env=self.env,
        )
        assert self._proc.stdin and self._proc.stdout and self._proc.stderr
        transport = StdioTransport(self._proc.stdout, self._proc.stdin)
        self._endpoint = Endpoint(transport, name=self.name, on_malformed=self._on_malformed)
        self._endpoint.start()
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(self._proc.stderr), name=f"stderr:{self.name}"
        )
        asyncio.create_task(self._watch_exit(), name=f"exit-watch:{self.name}")
        return self._endpoint

    async def _watch_exit(self) -> None:
        assert self._proc is not None
        code = await self._proc.wait()
        self._exited.set()
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


def base_env(passthrough: list[str], extra: dict[str, str] | None = None) -> dict[str, str]:
    """A scrubbed environment: nothing from the parent except an explicit allowlist, plus the
    variables a brick always needs to run at all, plus kernel-injected extras.

    On Windows a child process cannot start without ``SystemRoot`` (and friends); those, plus the
    interpreter's own path variables, are always included. Everything else must be named in the
    brick manifest's ``env_passthrough``.
    """
    always = ["SYSTEMROOT", "SYSTEMDRIVE", "PATH", "PATHEXT", "TEMP", "TMP", "WINDIR", "COMSPEC"]
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
