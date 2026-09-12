"""Per-brick supervision: start, initialize, health check, crash detection, restart with backoff.

One :class:`BrickSupervisor` owns one brick's process across its whole life, including across
restarts. The kernel process itself never dies because a brick did.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from veridian.contracts import PROTOCOL_VERSION, is_compatible_protocol
from veridian.contracts.errors import ProtocolError
from veridian.kernel.errors import BrickStartError
from veridian.kernel.events import (
    BRICK_CRASHED,
    BRICK_MALFORMED_LINE,
    BRICK_READY,
    BRICK_RESTART_EXHAUSTED,
    BRICK_RESTARTING,
    BRICK_STARTING,
    BRICK_STDERR,
    BRICK_STOPPED,
    EventBus,
)
from veridian.kernel.config import ResolvedBinding
from veridian.plugin_runtime.ipc import Endpoint
from veridian.plugin_runtime.process import (
    BrickProcess,
    ContainerUnavailable,
    EngineCommandError,
    base_env,
    spawn_strategy_for,
)
from veridian.plugin_runtime.registry import BrickHandle, cross_check_capabilities, parse_capabilities_result

RequestRouter = Callable[[str, list[str], str, dict], Awaitable[object]]
RebindHook = Callable[[BrickHandle], Awaitable[None]]


@dataclass
class RestartPolicy:
    max_restarts: int = 3
    backoff_base: float = 0.5
    backoff_cap: float = 10.0
    window_seconds: float = 60.0

    def backoff_for(self, attempt: int) -> float:
        return min(self.backoff_cap, self.backoff_base * (2 ** max(0, attempt - 1)))


@dataclass
class BrickSupervisor:
    binding: ResolvedBinding
    workspace_root: Path
    effective_capabilities: list[str]
    bus: EventBus
    router: RequestRouter
    on_rebind: RebindHook | None = None
    restart_policy: RestartPolicy = field(default_factory=RestartPolicy)
    kill_timeout: float = 5.0
    init_timeout: float = 15.0

    handle: BrickHandle | None = field(default=None, init=False)
    _process: BrickProcess | None = field(default=None, init=False)
    _restart_times: list[float] = field(default_factory=list, init=False)
    _stopped: bool = field(default=False, init=False)
    _restart_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def name(self) -> str:
        return self.binding.manifest.name

    @property
    def contract(self) -> str:
        return self.binding.contract

    # -- start ----------------------------------------------------------------------

    async def start(self) -> BrickHandle:
        self._stopped = False
        self.handle = await self._spawn_and_init()
        return self.handle

    async def _spawn_and_init(self) -> BrickHandle:
        manifest = self.binding.manifest
        self.bus.emit_type(BRICK_STARTING, source=self.name, contract=self.contract)
        brick_env = base_env(manifest.env_passthrough, self.binding.env)
        try:
            spawn = spawn_strategy_for(manifest).resolve(
                manifest, workspace_root=self.workspace_root, brick_env=brick_env
            )
        except ContainerUnavailable as exc:
            raise BrickStartError(f"{self.name}: {exc}") from exc
        proc = BrickProcess(
            manifest.name,
            spawn.argv,
            cwd=Path(spawn.cwd),
            env=spawn.env,
            kill_timeout=self.kill_timeout,
            on_stderr=lambda ln: self.bus.emit_type(BRICK_STDERR, source=self.name, line=ln),
            on_malformed=lambda raw: self.bus.emit_type(BRICK_MALFORMED_LINE, source=self.name, raw=raw),
            on_exit=self._on_process_exit,
            pre_run=spawn.pre_run,
            post_run=spawn.post_run,
        )
        try:
            endpoint = await proc.start()
        except EngineCommandError as exc:
            raise BrickStartError(f"{self.name}: container setup failed: {exc}") from exc
        endpoint.on_request(self._make_request_handler())
        endpoint.on_notification(self._make_notification_handler())

        try:
            init = await endpoint.call(
                "plugin.initialize",
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "capabilities": list(self.effective_capabilities),
                    "workspace_root": str(self.workspace_root),
                    "config": self.binding.config,
                },
                timeout=self.init_timeout,
            )
            if not init.get("ready", False):
                raise BrickStartError(f"{self.name}: reported not ready: {init.get('detail', '')}")
            if not is_compatible_protocol(init.get("protocol_version")):
                raise BrickStartError(
                    f"{self.name}: protocol {init.get('protocol_version')!r} is incompatible with "
                    f"kernel {PROTOCOL_VERSION!r} (major version must match)"
                )
            caps = await endpoint.call("plugin.capabilities", {}, timeout=self.init_timeout)
            reported = parse_capabilities_result(caps)
            cross_check_capabilities(manifest, reported)
        except (ProtocolError, BrickStartError):
            await proc.stop()
            raise

        self._process = proc
        handle = BrickHandle(
            manifest=manifest,
            process=proc,
            endpoint=endpoint,
            reported_contracts=reported,
            granted_capabilities=list(self.effective_capabilities),
            call_timeout=self.binding.call_timeout,
        )
        self.bus.emit_type(BRICK_READY, source=self.name, contract=self.contract, pid=proc.pid)
        return handle

    def _make_request_handler(self):
        async def handler(method: str, params: dict) -> object:
            return await self.router(self.name, self.effective_capabilities, method, params)

        return handler

    def _make_notification_handler(self):
        async def handler(method: str, params: dict) -> None:
            # host.log arrives as a notification (protocol spec section 4.2).
            await self.router(self.name, self.effective_capabilities, method, params)

        return handler

    # -- health -------------------------------------------------------------------

    async def health_check(self, timeout: float = 3.0) -> bool:
        if not self.handle or not self._process or not self._process.alive:
            return False
        try:
            await self.handle.endpoint.call("plugin.ping", {"nonce": "hc"}, timeout=timeout)
            return True
        except ProtocolError:
            return False

    # -- crash + restart --------------------------------------------------------------

    def _on_process_exit(self, code: int) -> None:
        if self._stopped:
            return
        asyncio.ensure_future(self._handle_crash(code))

    async def _handle_crash(self, code: int) -> None:
        async with self._restart_lock:
            if self._stopped:
                return
            self.bus.emit_type(
                BRICK_CRASHED, source=self.name, contract=self.contract, exit_code=code
            )
            self.handle = None

            now = time.monotonic()
            self._restart_times = [t for t in self._restart_times if now - t < self.restart_policy.window_seconds]
            if len(self._restart_times) >= self.restart_policy.max_restarts:
                self.bus.emit_type(
                    BRICK_RESTART_EXHAUSTED,
                    source=self.name,
                    contract=self.contract,
                    restarts=len(self._restart_times),
                )
                return

            attempt = len(self._restart_times) + 1
            delay = self.restart_policy.backoff_for(attempt)
            self._restart_times.append(now)
            self.bus.emit_type(
                BRICK_RESTARTING, source=self.name, contract=self.contract, attempt=attempt, delay=delay
            )
            await asyncio.sleep(delay)
            if self._stopped:
                return
            try:
                self.handle = await self._spawn_and_init()
            except (ProtocolError, BrickStartError) as exc:
                self.bus.emit_type(
                    BRICK_CRASHED, source=self.name, contract=self.contract, exit_code=-1, detail=str(exc)
                )
                return
            if self.on_rebind:
                await self.on_rebind(self.handle)

    # -- stop -------------------------------------------------------------------

    async def stop(self) -> None:
        self._stopped = True
        proc = self._process
        if proc is None:
            return
        try:
            if proc.alive:
                try:
                    await proc.endpoint.call("plugin.shutdown", {}, timeout=3.0)
                except ProtocolError:
                    pass
        finally:
            await proc.stop()
            self.bus.emit_type(BRICK_STOPPED, source=self.name, contract=self.contract)
