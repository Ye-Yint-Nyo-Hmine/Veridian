"""A standalone, stdlib-only brick used to prove container spawn mode and egress control
(Milestone 2 / A2 + A3).

It implements the ``tools`` contract with one tool, ``probe``, that makes an HTTPS ``GET`` to a
host and reports whether it got through. ``urllib`` honours the ``HTTPS_PROXY`` the kernel injects
for an allow-listed brick, so: ``isolation.network = false`` -> no route, fails;
``network = true`` + host on ``allow_hosts`` -> succeeds via the egress proxy; host not on the
list -> the proxy answers 403, fails.

No parent-directory imports on purpose: the whole brick directory is mounted at ``/brick`` and
nothing else comes with it.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from typing import Any

NAME = "toolbox/containerized"


def _write(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _probe(args: dict[str, Any]) -> dict[str, Any]:
    host = args.get("host", "example.com")
    port = int(args.get("port", 443))
    url = f"https://{host}:{port}/" if port != 443 else f"https://{host}/"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:  # noqa: S310 - test fixture
            return {"reachable": True, "detail": f"HTTP {resp.status} from {host}"}
    except Exception as exc:  # noqa: BLE001 - any failure means "did not get through"
        return {"reachable": False, "detail": f"{type(exc).__name__}: {exc}"}


def main() -> None:
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
                "brick": {"name": NAME, "version": "0.1.0"}, "ready": True}})
        elif method == "plugin.capabilities":
            _write({"jsonrpc": "2.0", "id": rid, "result": {"contracts": [
                {"name": "tools", "version": "1.0", "methods": ["list", "invoke"]}]}})
        elif method == "plugin.ping":
            _write({"jsonrpc": "2.0", "id": rid,
                    "result": {"nonce": params["nonce"]} if "nonce" in params else {}})
        elif method == "plugin.shutdown":
            _write({"jsonrpc": "2.0", "id": rid, "result": {}})
            return
        elif method == "tools.list":
            _write({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": "probe", "description": "TCP-connect to host:port", "input_schema": {"type": "object"}}]}})
        elif method == "tools.invoke":
            if params.get("name") == "probe":
                res = _probe(params.get("input") or {})
                _write({"jsonrpc": "2.0", "id": rid,
                        "result": {"output": json.dumps(res), "is_error": not res["reachable"]}})
            else:
                _write({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32004, "message": f"no tool {params.get('name')!r}"}})
        elif rid is not None:
            _write({"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
