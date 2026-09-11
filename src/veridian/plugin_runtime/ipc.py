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
import contextvars
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

#: Set for the duration of an inbound request handler so a streaming handler can discover the
#: request id its deltas must be keyed by (protocol spec section 5).
current_request_id: contextvars.ContextVar[int | str | None] = contextvars.ContextVar(
    "veridian_current_request_id", default=None
)

from veridian.contracts.errors import (
    BRICK_UNAVAILABLE,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    REQUEST_CANCELLED,
    TIMEOUT,
    ProtocolError,
)
from veridian.plugin_runtime.transport import Transport, TransportClosed

RequestHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]
NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
MalformedHandler = Callable[[str], None]

_DEFAULT_CALL_TIMEOUT = 30.0
_DEFAULT_STREAM_TIMEOUT = 300.0

#: Notification the sender emits to ask the peer to abandon an in-flight request it originated
#: (protocol spec §5.1, ``veridian/1.1``). The ``$/`` prefix marks a framing-level control message
#: that carries no contract payload. A ``veridian/1.0`` peer ignores it and the call runs to its
#: timeout.
CANCEL_METHOD = "$/cancel"


class StreamCall:
    """Handle for an in-flight streaming call. Iterate for delta ``params`` dicts, then await
    :meth:`result` for the final response payload."""

    def __init__(self) -> None:
        self._deltas: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._result: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        #: wired by :meth:`Endpoint.call_stream` so the consumer can cancel the call
        self._endpoint: "Endpoint | None" = None
        self._req_id: int | str | None = None
        self._request_future: asyncio.Future[Any] | None = None
        self._cancelled = False

    async def cancel(self) -> None:
        """Abandon this streaming call: send ``$/cancel`` to the peer and finish the local stream
        with a ``request_cancelled`` error. Idempotent. A ``veridian/1.0`` peer ignores the
        notification, in which case the call still stops locally but the brick runs on until its
        own timeout."""
        if self._cancelled:
            return
        self._cancelled = True
        if self._endpoint is not None and self._req_id is not None:
            await self._endpoint._send_cancel(self._req_id)
        if self._request_future is not None and not self._request_future.done():
            self._request_future.cancel()
        self._finish_err(
            ProtocolError(REQUEST_CANCELLED, f"stream {self._req_id} cancelled by caller")
        )

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
        #: in-flight *inbound* request handlers, keyed by the peer's request id, so a ``$/cancel``
        #: naming that id can cancel the task running it.
        self._inbound_by_id: dict[int | str, asyncio.Task[None]] = {}
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

    async def wait_closed(self) -> None:
        """Block until the peer stream has ended (EOF, reset, or :meth:`aclose`)."""
        await self._closed.wait()

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
            self._fire_cancel(req_id)
            raise ProtocolError(TIMEOUT, f"{self.name}: call {method} timed out after {timeout}s") from None
        except asyncio.CancelledError:
            # The awaiting task is being torn down (e.g. Ctrl-C upstream). Tell the peer to stop
            # so the request does not run to completion detached, then propagate.
            self._fire_cancel(req_id)
            raise
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
        stream._endpoint = self
        stream._req_id = req_id
        stream._request_future = fut
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

    async def _send_cancel(self, req_id: int | str) -> None:
        """Best-effort ``$/cancel`` for a request this endpoint originated. Never raises: a peer
        that has already gone away needs no telling."""
        try:
            await self.notify(CANCEL_METHOD, {"id": req_id})
        except ProtocolError:
            pass

    def _fire_cancel(self, req_id: int | str) -> None:
        """Schedule :meth:`_send_cancel` without awaiting — safe to call from inside an
        ``except CancelledError`` block, where a bare ``await`` would re-raise immediately."""
        try:
            task = asyncio.ensure_future(self._send_cancel(req_id))
        except RuntimeError:  # no running loop (shutdown) — nothing to cancel
            return
        self._inbound_tasks.add(task)
        task.add_done_callback(self._inbound_tasks.discard)

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
            rid = msg["id"]
            task = asyncio.create_task(self._handle_request(msg), name=f"ipc-req:{self.name}")
            self._inbound_tasks.add(task)
            self._inbound_by_id[rid] = task

            def _done(t: asyncio.Task[None], rid: int | str = rid) -> None:
                self._inbound_tasks.discard(t)
                if self._inbound_by_id.get(rid) is t:
                    del self._inbound_by_id[rid]

            task.add_done_callback(_done)
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

        # $/cancel names a request this endpoint is *serving*; cancel the task running it. The
        # handler's own awaits (including any host.contract.call it made) unwind, which is how the
        # cancel cascades down a chain of bricks.
        if method == CANCEL_METHOD:
            target = self._inbound_by_id.get(params.get("id"))
            if target is not None and not target.done():
                target.cancel()
            return

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
        token = current_request_id.set(req_id)
        try:
            result = await self._request_handler(method, params)
        except ProtocolError as exc:
            await self._send_error(req_id, exc.code, exc.message, exc.data)
        except asyncio.CancelledError:
            # Tell the originator the request ended because it was cancelled. Best-effort: the
            # originator has usually stopped waiting already, and we must not swallow the cancel.
            self._fire_send_error(req_id, REQUEST_CANCELLED, f"{method} cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - map any handler crash to a JSON-RPC error
            await self._send_error(req_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
        else:
            await self._safe_send({"jsonrpc": "2.0", "id": req_id, "result": result})
        finally:
            current_request_id.reset(token)

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

    def _fire_send_error(
        self, req_id: int | str, code: int, message: str, data: dict[str, Any] | None = None
    ) -> None:
        """Schedule an error response without awaiting — for use from an ``except CancelledError``
        block."""
        try:
            task = asyncio.ensure_future(self._send_error(req_id, code, message, data))
        except RuntimeError:
            return
        self._inbound_tasks.add(task)
        task.add_done_callback(self._inbound_tasks.discard)

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
