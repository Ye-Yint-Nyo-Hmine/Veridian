"""Returns results that violate the context schema on purpose."""

import json
import sys


def w(o):
    sys.stdout.write(json.dumps(o) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    m = json.loads(line)
    method, rid, params = m.get("method"), m.get("id"), m.get("params") or {}
    if method == "plugin.initialize":
        w({"jsonrpc": "2.0", "id": rid, "result": {
            "protocol_version": "veridian/1.0", "brick": {"name": "nonconformant/context-liar", "version": "0.0.1"},
            "ready": True}})
    elif method == "plugin.capabilities":
        w({"jsonrpc": "2.0", "id": rid, "result": {
            "contracts": [{"name": "context", "version": "1.0", "methods": ["index", "retrieve", "invalidate"]}]}})
    elif method == "plugin.ping":
        w({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "plugin.shutdown":
        w({"jsonrpc": "2.0", "id": rid, "result": {}})
        break
    elif method == "context.index":
        w({"jsonrpc": "2.0", "id": rid, "result": {"indexed": -5}})            # minimum is 0
    elif method == "context.retrieve":
        w({"jsonrpc": "2.0", "id": rid, "result": {"chunks": "not-a-list"}})   # wrong type
    elif method == "context.invalidate":
        w({"jsonrpc": "2.0", "id": rid, "result": {}})                          # missing 'invalidated'
    elif rid is not None:
        w({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no"}})
