"""A dependency-free JSON-RPC brick skeleton for the ``echo`` fixture contract.

``echo`` is a throwaway contract used only by the kernel's own tests. It has no schema under
``schemas/`` and nothing in production implements it. Its whole job is to give the plugin runtime
a cooperative peer to talk to, and — in the deliberately broken variants — an uncooperative one.

Written against nothing but the standard library on purpose: a real brick would use the SDK, but a
fixture proving the SDK-less wire contract must not.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

NAME = "echo/unnamed"
VERSION = "0.0.0"
METHODS = ["say", "stream"]

_host_id = 0
_state: dict[str, Any] = {}


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def respond(req_id: Any, result: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "result": result})


def error(req_id: Any, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def notify(method: str, params: dict[str, Any]) -> None:
    _write({"jsonrpc": "2.0", "method": method, "params": params})


def _iter_stdin():
    for line in sys.stdin:
        line = line.strip()
        if line:
            yield json.loads(line)


def host_call(method: str, params: dict[str, Any]) -> Any:
    """Synchronous host.* call. While waiting for the response, keep dispatching any
    kernel-originated requests that arrive — otherwise a host.contract.call that routes back to
    this same brick would deadlock."""
    global _host_id
    _host_id += 1
    hid = f"h{_host_id}"
    _write({"jsonrpc": "2.0", "id": hid, "method": method, "params": params})
    for msg in _iter_stdin():
        if msg.get("id") == hid:
            if "error" in msg:
                raise RuntimeError(f"host call {method} failed: {msg['error']}")
            return msg["result"]
        _dispatch(msg)
    raise RuntimeError("stdin closed before host response")


def _dispatch(msg: dict[str, Any]) -> None:
    handlers: dict[str, Callable[[Any, dict[str, Any]], None]] = _state["handlers"]
    name = _state["name"]
    version = _state["version"]
    reported = _state["reported"]

    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}

    if method in handlers:
        handlers[method](req_id, params)
    elif method == "plugin.initialize":
        respond(req_id, {"protocol_version": params.get("protocol_version", "veridian/1.0"),
                         "brick": {"name": name, "version": version}, "ready": True})
    elif method == "plugin.capabilities":
        respond(req_id, {"contracts": [{"name": "echo", "version": "1.0", "methods": reported}]})
    elif method == "plugin.ping":
        respond(req_id, {"nonce": params["nonce"]} if "nonce" in params else {})
    elif method == "plugin.shutdown":
        respond(req_id, {})
        raise SystemExit(0)
    elif method == "echo.say":
        if _state["on_say"] is not None:
            _state["on_say"](req_id, params)
        else:
            respond(req_id, {"text": params.get("text", "")})
    elif method == "echo.stream":
        text = params.get("text", "")
        chunks = int(params.get("chunks", 3))
        for i in range(chunks):
            notify("echo.delta", {"request_id": req_id, "index": i, "text": text})
        respond(req_id, {"text": text, "chunks": chunks})
    elif req_id is not None:
        error(req_id, -32601, f"method not found: {method}")


def run(
    *,
    name: str = NAME,
    version: str = VERSION,
    reported_methods: list[str] | None = None,
    on_say: Callable[[Any, dict[str, Any]], None] | None = None,
    extra: dict[str, Callable[[Any, dict[str, Any]], None]] | None = None,
) -> None:
    _state.update(
        name=name,
        version=version,
        reported=reported_methods if reported_methods is not None else METHODS,
        on_say=on_say,
        handlers=extra or {},
    )
    for msg in _iter_stdin():
        _dispatch(msg)
