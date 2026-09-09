"""The JSON-RPC 2.0 codec.

An :class:`Endpoint` wraps a :class:`~veridian.plugin_runtime.transport.Transport` and provides:

* outbound :meth:`call` with per-call timeouts and error mapping,
* outbound :meth:`call_stream` for request-id-keyed streaming (protocol spec section 5),
* :meth:`notify` for fire-and-forget notifications,
* inbound request / notification dispatch on background tasks so a slow handler never blocks the
  read loop,
* malformed-line quarantine: a bad line is counted and reported, not fatal, until a threshold of
  consecutive bad lines marks the peer as crashed.

The endpoint is peer-symmetric: the kernel uses it to drive a brick, and the SDK uses the same
class to serve one.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from veridian.contracts.errors import (
    BRICK_UNAVAILABLE,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    TIMEOUT,
    ProtocolError,
)
from veridian.plugin_runtime.transport import Transport, TransportClosed

RequestHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]
NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
MalformedHandler = Callable[[str], None]

_DEFAULT_CALL_TIMEOUT = 30.0
_DEFAULT_STREAM_TIMEOUT = 300.0


class StreamCall:
    """Handle for an in-flight streaming call. Iterate for delta ``params`` dicts, then await
    :meth:`result` for the final response payload."""

    def __init__(self) -> None:
        self._deltas: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()

    def _push_delta(self, params: dict[str, Any]) -> None:
        self._deltas.put_nowait(params)

    def _finish_ok(self, value: Any) -> None:
        if not self._result.done():
            self._result.set_result(value)
        self._deltas.put_nowait(None)

    def _finish_err(self, exc: BaseException) -> None:
        if not self._result.done():
            self._result.set_exception(exc)
        self._deltas.put_nowait(None)

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._deltas.get()
            if item is None:
                return
            yield item

    async def result(self) -> Any:
        return await self._result


class Endpoint:
    def __init__(
        self,
        transport: Transport,
        *,
        name: str = "peer",
        on_malformed: MalformedHandler | None = None,
        malformed_threshold: int = 5,
    ) -> None:
        self._t = transport
        self.name = name
        self._on_malformed = on_malformed
        self._malformed_threshold = malformed_threshold

        self._next_id = 0
        self._pending: dict[int | str, asyncio.Future[Any]] = {}
        self._streams: dict[int | str, StreamCall] = {}
        self._request_handler: RequestHandler | None = None
        self._notification_handler: NotificationHandler | None = None

        self._read_task: asyncio.Task[None] | None = None
        self._inbound_tasks: set[asyncio.Task[None]] = set()
        self._closed = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        if self._read_task is None:
            self._read_task = asyncio.create_task(self._read_loop(), name=f"ipc-read:{self.name}")

    async def aclose(self) -> None:
        self._closed.set()
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        for task in list(self._inbound_tasks):
            task.cancel()
        self._fail_all(ProtocolError(BRICK_UNAVAILABLE, f"{self.name}: endpoint closed"))
        await self._t.close()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def on_request(self, handler: RequestHandler) -> None:
        self._request_handler = handler

    def on_notification(self, handler: NotificationHandler) -> None:
        self._notification_handler = handler

    # -- outbound -------------------------------------------------------------------

    def _alloc_id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def call(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = _DEFAULT_CALL_TIMEOUT
    ) -> Any:
        req_id = self._alloc_id()
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        try:
            if timeout is None:
                return await fut
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise ProtocolError(TIMEOUT, f"{self.name}: call {method} timed out after {timeout}s") from None
        finally:
            self._pending.pop(req_id, None)

    async def call_stream(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = _DEFAULT_STREAM_TIMEOUT,
    ) -> StreamCall:
        req_id = self._alloc_id()
        stream = StreamCall()
        self._streams[req_id] = stream
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        await self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})

        async def _settle() -> None:
            try:
                value = await (fut if timeout is None else asyncio.wait_for(fut, timeout))
            except asyncio.TimeoutError:
                stream._finish_err(ProtocolError(TIMEOUT, f"{self.name}: stream {method} timed out"))
            except BaseException as exc:  # noqa: BLE001 - propagated to the consumer
                stream._finish_err(exc)
            else:
                stream._finish_ok(value)
            finally:
                self._pending.pop(req_id, None)
                self._streams.pop(req_id, None)

        asyncio.create_task(_settle(), name=f"ipc-stream:{self.name}:{req_id}")
        return stream

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def _write(self, message: dict[str, Any]) -> None:
        try:
            await self._t.send(json.dumps(message, separators=(",", ":")))
        except TransportClosed as exc:
            raise ProtocolError(BRICK_UNAVAILABLE, f"{self.name}: {exc}") from exc

    # -- inbound -------------------------------------------------------------------

    async def _read_loop(self) -> None:
        consecutive_bad = 0
        try:
            async for line in self._t.lines():
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    consecutive_bad += 1
                    self._report_malformed(line, consecutive_bad)
                    if consecutive_bad >= self._malformed_threshold:
                        break
                    continue
                if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                    consecutive_bad += 1
                    self._report_malformed(line, consecutive_bad)
                    if consecutive_bad >= self._malformed_threshold:
                        break
                    continue
                consecutive_bad = 0
                self._dispatch(msg)
        except asyncio.CancelledError:
            raise
        finally:
            self._closed.set()
            self._fail_all(ProtocolError(BRICK_UNAVAILABLE, f"{self.name}: peer stream ended"))

    def _report_malformed(self, raw: str, count: int) -> None:
        if self._on_malformed:
            self._on_malformed(raw[:2000])

    def _dispatch(self, msg: dict[str, Any]) -> None:
        has_id = "id" in msg and msg["id"] is not None
        is_response = "result" in msg or "error" in msg

        if is_response and has_id:
            self._resolve(msg)
            return
        if "method" in msg and has_id:
            task = asyncio.create_task(self._handle_request(msg), name=f"ipc-req:{self.name}")
            self._inbound_tasks.add(task)
            task.add_done_callback(self._inbound_tasks.discard)
            return
        if "method" in msg:  # notification
            self._handle_notification(msg)
            return
        self._report_malformed(json.dumps(msg), 1)

    def _resolve(self, msg: dict[str, Any]) -> None:
        fut = self._pending.get(msg["id"])
        if fut is None or fut.done():
            return
        if "error" in msg:
            fut.set_exception(ProtocolError.from_error_object(msg["error"]))
        else:
            fut.set_result(msg["result"])

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        params = msg.get("params") or {}
        method = msg["method"]
        # Streaming deltas are notifications carrying params.request_id (protocol spec section 5).
        rid = params.get("request_id")
        if rid is not None and rid in self._streams:
            self._streams[rid]._push_delta(params)
            return
        if self._notification_handler is None:
            return
        task = asyncio.create_task(
            self._safe_notification(method, params), name=f"ipc-note:{self.name}"
        )
        self._inbound_tasks.add(task)
        task.add_done_callback(self._inbound_tasks.discard)

    async def _safe_notification(self, method: str, params: dict[str, Any]) -> None:
        try:
            assert self._notification_handler is not None
            await self._notification_handler(method, params)
        except Exception:  # noqa: BLE001 - a bad notification handler must not kill the endpoint
            pass

    async def _handle_request(self, msg: dict[str, Any]) -> None:
        req_id = msg["id"]
        method = msg["method"]
        params = msg.get("params") or {}
        if self._request_handler is None:
            await self._send_error(req_id, METHOD_NOT_FOUND, f"no request handler for {method}")
            return
        try:
            result = await self._request_handler(method, params)
        except ProtocolError as exc:
            await self._send_error(req_id, exc.code, exc.message, exc.data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - map any handler crash to a JSON-RPC error
            await self._send_error(req_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        else:
            await self._safe_send({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def emit_delta(self, request_id: int | str, contract: str, delta: dict[str, Any]) -> None:
        """Helper for a serving endpoint: send a ``<contract>.delta`` notification for a streaming
        request currently being handled."""
        await self.notify(f"{contract}.delta", {"request_id": request_id, **delta})

    async def _send_error(
        self, req_id: int | str, code: int, message: str, data: dict[str, Any] | None = None
    ) -> None:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        await self._safe_send({"jsonrpc": "2.0", "id": req_id, "error": err})

    async def _safe_send(self, message: dict[str, Any]) -> None:
        try:
            await self._write(message)
        except ProtocolError:
            pass

    def _fail_all(self, exc: ProtocolError) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
                # If nothing is awaiting this future any more, make sure the exception is
                # considered retrieved so asyncio does not log it at GC time.
                fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        self._pending.clear()
        for stream in list(self._streams.values()):
            stream._finish_err(exc)
        self._streams.clear()


def parse_message(line: str) -> dict[str, Any]:
    """Parse and minimally validate one wire line. Raises :class:`ProtocolError` (INVALID_REQUEST)."""
    try:
        msg = json.loads(line)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError(INVALID_REQUEST, f"not JSON: {exc}") from exc
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        raise ProtocolError(INVALID_REQUEST, "not a JSON-RPC 2.0 message")
    return msg
