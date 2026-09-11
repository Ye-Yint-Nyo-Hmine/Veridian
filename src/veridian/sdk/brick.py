"""The Python brick SDK.

A brick author subclasses :class:`Brick`, decorates handler methods with :func:`rpc`, and calls
:func:`run`. The SDK handles framing, lifecycle, dispatch, host-service calls, streaming deltas,
and error mapping. It is deliberately thin: protocol *semantics* live in the schemas and the
kernel, not here.

    from veridian.sdk import Brick, rpc, run

    class MyEngine(Brick):
        name = "inference/my-engine"
        version = "0.1.0"
        implements = {"inference": ["generate", "models"]}

        @rpc("inference.generate")
        async def generate(self, params, ctx):
            out = await self.host.contract_call("model_provider", "complete", {...})
            return {...}

    if __name__ == "__main__":
        run(MyEngine())
"""

from __future__ import annotations

import asyncio
import os
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from veridian.contracts import (
    CONTRACTS,
    PROTOCOL_VERSION,
    SchemaValidationError,
    is_compatible_protocol,
)
from veridian.contracts.errors import (
    INVALID_PARAMS,
    PROTOCOL_VERSION_MISMATCH,
    UNSUPPORTED_METHOD,
    ProtocolError,
)
from veridian.plugin_runtime.ipc import Endpoint, current_request_id
from veridian.sdk.host import HostProxy
from veridian.sdk.transport import ThreadedStdioTransport

Handler = Callable[..., Awaitable[Any]]


def _log_brick_error(brick: str, method: str, tb: str) -> str | None:
    """Append a brick-handler traceback to ``VERIDIAN_HOME/logs/brick-errors.log`` and return the
    file path. A handler crash otherwise reaches the user as a single line with no brick name and
    no traceback — impossible to report. Best-effort: a logging failure must never mask the
    original error."""
    try:
        from veridian.plugin_runtime.home import veridian_home

        log_dir = veridian_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "brick-errors.log"
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n===== {stamp}  {brick}  {method} =====\n{tb}")
        return str(path)
    except Exception:  # noqa: BLE001
        return None


def rpc(method: str, *, streaming: bool = False) -> Callable[[Handler], Handler]:
    """Mark a coroutine as the handler for ``<contract>.<method>``."""

    def deco(fn: Handler) -> Handler:
        fn.__veridian_rpc__ = (method, streaming)  # type: ignore[attr-defined]
        return fn

    return deco


class RequestContext:
    """Passed to every handler. Carries the brick config and a delta emitter for streaming."""

    def __init__(self, brick: "Brick", method: str) -> None:
        self.brick = brick
        self.method = method
        self.config = brick.config
        self.workspace_root = brick.workspace_root

    async def emit_delta(self, delta: dict[str, Any]) -> None:
        rid = current_request_id.get()
        if rid is None:  # pragma: no cover - defensive
            raise RuntimeError("emit_delta called outside a request handler")
        contract = self.method.split(".", 1)[0]
        assert self.brick._endpoint is not None
        await self.brick._endpoint.notify(f"{contract}.delta", {"request_id": rid, **delta})


class BrickError(ProtocolError):
    """Raise from a handler to return a specific JSON-RPC error. Defaults to brick_internal_error."""

    def __init__(self, message: str, code: int = -32004, data: dict[str, Any] | None = None) -> None:
        super().__init__(code, message, data)


class Brick:
    name: str = "unnamed/brick"
    version: str = "0.1.0"
    implements: dict[str, list[str]] = {}

    def __init__(self) -> None:
        self.config: dict[str, Any] = {}
        self.capabilities: list[str] = []
        self.workspace_root: str = "."
        self.host: HostProxy | None = None
        self._endpoint: Endpoint | None = None
        self._stop = asyncio.Event()
        self._handlers: dict[str, tuple[Handler, bool]] = {}
        for attr in dir(type(self)):
            fn = getattr(self, attr, None)
            meta = getattr(fn, "__veridian_rpc__", None)
            if meta:
                self._handlers[meta[0]] = (fn, meta[1])

    # -- lifecycle hooks an author may override ---------------------------------------

    async def on_initialize(self) -> bool:
        """Return False to report ``ready: false``. Default: ready."""
        return True

    async def on_shutdown(self) -> None:
        pass

    # -- serving ------------------------------------------------------------------

    async def serve(self) -> None:
        transport = ThreadedStdioTransport()
        endpoint = Endpoint(transport, name=self.name)
        self._endpoint = endpoint
        self.host = HostProxy(endpoint)
        endpoint.on_request(self._dispatch)
        endpoint.on_notification(self._dispatch_notification)
        endpoint.start()
        try:
            # Stop on ``plugin.shutdown`` *or* on the kernel going away — an abrupt pipe close
            # (the kernel died, or a Ctrl-C reached it first) must end the brick cleanly, not
            # leave it parked in this wait with a dead peer.
            stop = asyncio.ensure_future(self._stop.wait())
            gone = asyncio.ensure_future(endpoint.wait_closed())
            try:
                await asyncio.wait({stop, gone}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for fut in (stop, gone):
                    fut.cancel()
        finally:
            await endpoint.aclose()

    async def _dispatch_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "plugin.initialize":
            return await self._initialize(params)
        if method == "plugin.capabilities":
            return {
                "contracts": [
                    {"name": c, "version": "1.0", "methods": list(m)} for c, m in self.implements.items()
                ]
            }
        if method == "plugin.ping":
            return {"nonce": params["nonce"]} if "nonce" in params else {}
        if method == "plugin.shutdown":
            await self.on_shutdown()
            self._stop.set()
            return {}

        entry = self._handlers.get(method)
        if entry is None:
            raise ProtocolError(UNSUPPORTED_METHOD, f"{self.name} does not implement {method!r}")
        fn, _streaming = entry
        self._validate_params(method, params)
        ctx = RequestContext(self, method)
        try:
            return await fn(params, ctx)
        except ProtocolError:
            raise
        except Exception as exc:  # noqa: BLE001 - authors' bugs become brick_internal_error
            tb = traceback.format_exc()
            _log_brick_error(self.name, method, tb)
            raise BrickError(
                f"{type(exc).__name__}: {exc}",
                data={"brick": self.name, "method": method, "traceback": tb},
            ) from exc

    async def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if not is_compatible_protocol(params.get("protocol_version")):
            raise ProtocolError(
                PROTOCOL_VERSION_MISMATCH,
                f"{self.name} speaks {PROTOCOL_VERSION!r}, kernel offered "
                f"{params.get('protocol_version')!r} (major version must match)",
            )
        self.capabilities = list(params.get("capabilities", []))
        self.workspace_root = params.get("workspace_root", ".")
        self.config = dict(params.get("config", {}))
        ready = await self.on_initialize()
        return {
            "protocol_version": PROTOCOL_VERSION,
            "brick": {"name": self.name, "version": self.version},
            "ready": bool(ready),
        }

    def _validate_params(self, method: str, params: dict[str, Any]) -> None:
        contract, _, m = method.partition(".")
        spec = CONTRACTS.get(contract)
        if spec is None or m not in spec.methods:
            return
        try:
            spec.validate_params(m, params)
        except SchemaValidationError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc), exc.as_error_data()) from exc


def run(brick: Brick) -> None:
    """Serve ``brick`` until the kernel shuts it down. Call this from ``__main__``."""
    try:
        asyncio.run(brick.serve())
    except KeyboardInterrupt:
        # The kernel spawns bricks in their own process group so a console Ctrl-C reaches only
        # the kernel, which then stops each brick cleanly. If one still arrives here anyway,
        # leave *now* — before interpreter finalisation can race the SDK's daemon stdin-reader
        # thread (parked in a blocking read) into a Windows access violation.
        os._exit(0)
