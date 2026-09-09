"""A minimal alternate sandbox: runs commands with subprocess.run in a thread. Different code from
bricks/sandbox/local, identical contract — the kernel cannot tell them apart."""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

from veridian.sdk import Brick, rpc, run


class CustomSandbox(Brick):
    name = "example/thread-sandbox"
    version = "0.1.0"
    implements = {"sandbox": ["exec", "spawn", "write", "kill", "reset"]}

    async def on_initialize(self) -> bool:
        self.root = Path(self.workspace_root).resolve()
        self._procs: dict[str, subprocess.Popen] = {}
        return True

    @rpc("sandbox.exec")
    async def exec_(self, params, ctx):
        cmd = params["command"]
        timeout = params.get("timeout_ms", 30000) / 1000

        def _blocking():
            try:
                p = subprocess.run(
                    cmd, cwd=str(self.root), capture_output=True, text=True, timeout=timeout,
                    shell=isinstance(cmd, str), stdin=subprocess.DEVNULL,
                )
                return p.returncode, p.stdout, p.stderr, False
            except subprocess.TimeoutExpired as e:
                return -1, e.stdout or "", e.stderr or "", True

        code, out, err, to = await asyncio.to_thread(_blocking)
        return {"exit_code": code, "stdout": out[:100000], "stderr": err[:100000], "timed_out": to, "truncated": False}

    @rpc("sandbox.spawn")
    async def spawn(self, params, ctx):
        cmd = params["command"]
        p = subprocess.Popen(cmd, cwd=str(self.root), shell=isinstance(cmd, str))
        h = uuid.uuid4().hex
        self._procs[h] = p
        return {"handle": h}

    @rpc("sandbox.write")
    async def write(self, params, ctx):
        target = (self.root / params["path"]).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        data = params["content"].encode("utf-8")
        target.write_bytes(data)
        return {"bytes_written": len(data)}

    @rpc("sandbox.kill")
    async def kill(self, params, ctx):
        p = self._procs.pop(params["handle"], None)
        if p is None:
            return {"killed": False}
        p.terminate()
        return {"killed": True}

    @rpc("sandbox.reset")
    async def reset(self, params, ctx):
        for h in list(self._procs):
            self._procs.pop(h).terminate()
        return {}


if __name__ == "__main__":
    run(CustomSandbox())
