"""sandbox/local — run processes confined to a working directory.

Milestone 1 confinement is working-directory + path checks + timeouts + output caps. It is NOT an
OS sandbox: no namespace, cgroup, or job-object isolation. SECURITY.md says so plainly; container
isolation is a roadmap item.
"""

from __future__ import annotations

import asyncio
import base64
import os
import uuid
from pathlib import Path

from veridian.sdk import Brick, BrickError, rpc, run

_DEFAULT_TIMEOUT_MS = 30_000
_DEFAULT_MAX_OUTPUT = 1_000_000


class SandboxLocal(Brick):
    name = "sandbox/local"
    version = "0.1.0"
    implements = {"sandbox": ["exec", "spawn", "write", "kill", "reset"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.config.get("root") or self.workspace_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_output = int(self.config.get("max_output_bytes", _DEFAULT_MAX_OUTPUT))
        self.default_timeout_ms = int(self.config.get("default_timeout_ms", _DEFAULT_TIMEOUT_MS))
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        return True

    # -- helpers -------------------------------------------------------------------

    def _confine(self, rel: str | None) -> Path:
        target = (self.root / (rel or ".")).resolve()
        if target != self.root and self.root not in target.parents:
            raise BrickError(f"path {rel!r} escapes the sandbox root", code=-32602)
        return target

    def _env(self, extra: dict | None) -> dict[str, str]:
        env = {
            k: os.environ[k]
            for k in ("SYSTEMROOT", "SYSTEMDRIVE", "PATH", "PATHEXT", "TEMP", "TMP", "WINDIR", "COMSPEC")
            if k in os.environ
        }
        if extra:
            env.update({str(k): str(v) for k, v in extra.items()})
        return env

    async def _run(self, command, cwd: Path, env: dict, stdin: str | None, timeout_ms: int):
        if isinstance(command, str):
            proc = await asyncio.create_subprocess_shell(
                command, cwd=str(cwd), env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *command, cwd=str(cwd), env=env,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
        timed_out = False
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin.encode("utf-8") if stdin else None), timeout_ms / 1000
            )
        except asyncio.TimeoutError:
            timed_out = True
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            out, err = b"", b""
        truncated = len(out) > self.max_output or len(err) > self.max_output
        return {
            "exit_code": proc.returncode if proc.returncode is not None else -1,
            "stdout": out[: self.max_output].decode("utf-8", errors="replace"),
            "stderr": err[: self.max_output].decode("utf-8", errors="replace"),
            "timed_out": timed_out,
            "truncated": truncated,
        }

    # -- methods -------------------------------------------------------------------

    @rpc("sandbox.exec")
    async def exec_(self, params, ctx):
        cwd = self._confine(params.get("cwd"))
        cwd.mkdir(parents=True, exist_ok=True)
        timeout_ms = int(params.get("timeout_ms", self.default_timeout_ms))
        return await self._run(
            params["command"], cwd, self._env(params.get("env")), params.get("stdin"), timeout_ms
        )

    @rpc("sandbox.spawn")
    async def spawn(self, params, ctx):
        cwd = self._confine(params.get("cwd"))
        cwd.mkdir(parents=True, exist_ok=True)
        command = params["command"]
        if isinstance(command, str):
            proc = await asyncio.create_subprocess_shell(
                command, cwd=str(cwd), env=self._env(params.get("env")),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *command, cwd=str(cwd), env=self._env(params.get("env")),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        handle = uuid.uuid4().hex
        self._procs[handle] = proc
        return {"handle": handle}

    @rpc("sandbox.write")
    async def write(self, params, ctx):
        target = self._confine(params["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        encoding = params.get("encoding", "utf-8")
        data = base64.b64decode(params["content"]) if encoding == "base64" else params["content"].encode("utf-8")
        target.write_bytes(data)
        return {"bytes_written": len(data)}

    @rpc("sandbox.kill")
    async def kill(self, params, ctx):
        proc = self._procs.pop(params["handle"], None)
        if proc is None:
            return {"killed": False}
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        return {"killed": True}

    @rpc("sandbox.reset")
    async def reset(self, params, ctx):
        for handle in list(self._procs):
            await self.kill({"handle": handle}, ctx)
        return {}


if __name__ == "__main__":
    run(SandboxLocal())
