"""A dependency-free brick skeleton whose only real job is to import a pinned third-party package
and report which version it loaded.

Two copies of this brick declare incompatible pins of the same package (``six``) and bind
*different* contracts, so they can be loaded side by side in one stack. That only works if each
was installed into its own environment — which is exactly what A1 (per-brick dependency
isolation) buys. Written against the standard library so a fixture venv needs nothing but the
pinned package itself.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import six  # the whole point: a pinned third-party import

_CONTRACT_METHODS = {
    "tools": ["list", "invoke"],
    "workspace": ["read", "write", "list", "stat"],
}


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _handle(contract: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if contract == "tools":
        if method == "list":
            return {"tools": []}
        return {"output": f"no tools; running six {six.__version__}", "is_error": True}
    # workspace
    if method == "list":
        return {"entries": []}
    if method == "write":
        return {"path": params.get("path", ""), "bytes_written": len(params.get("content", ""))}
    if method == "stat":
        return {"path": params.get("path", ""), "type": "dir", "size": 0}
    # read: answer with a well-formed error — still conformant behaviour
    raise _CleanError(f"deps fixture has no file {params.get('path', '')!r}")


class _CleanError(Exception):
    pass


def run(*, name: str, contract: str) -> None:
    methods = _CONTRACT_METHODS[contract]
    print(f"{name}: loaded six {six.__version__}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method", "")
        rid = msg.get("id")
        params = msg.get("params") or {}

        if method == "plugin.initialize":
            _write({"jsonrpc": "2.0", "id": rid, "result": {
                "protocol_version": params.get("protocol_version", "veridian/1.0"),
                "brick": {"name": name, "version": "0.1.0"},
                "ready": True,
                "detail": f"six {six.__version__}",
            }})
        elif method == "plugin.capabilities":
            _write({"jsonrpc": "2.0", "id": rid, "result": {
                "contracts": [{"name": contract, "version": "1.0", "methods": methods}]
            }})
        elif method == "plugin.ping":
            _write({"jsonrpc": "2.0", "id": rid,
                    "result": {"nonce": params["nonce"]} if "nonce" in params else {}})
        elif method == "plugin.shutdown":
            _write({"jsonrpc": "2.0", "id": rid, "result": {}})
            return
        elif method.startswith(f"{contract}."):
            try:
                result = _handle(contract, method.split(".", 1)[1], params)
                _write({"jsonrpc": "2.0", "id": rid, "result": result})
            except _CleanError as exc:
                _write({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32004, "message": str(exc)}})
        elif rid is not None:
            _write({"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"method not found: {method}"}})
