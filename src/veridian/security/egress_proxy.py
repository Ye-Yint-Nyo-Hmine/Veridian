"""A stdlib-only allow-listing HTTPS/HTTP forward proxy.

The kernel starts one of these in a sidecar container next to a brick that declares
``isolation.network = true`` with a non-empty ``allow_hosts``. The brick container has no route to
the internet of its own (it sits on an ``--internal`` Docker network); its only way out is this
proxy, and the proxy refuses every ``CONNECT`` / absolute-URI request whose host:port is not on the
allowlist. That is what makes "every network destination in a stack is enumerable from the
manifests alone" true rather than aspirational.

Run as::

    VERIDIAN_ALLOW_HOSTS="api.anthropic.com:443,api.openai.com:443" python -m veridian.security.egress_proxy

Reads the allowlist from ``VERIDIAN_ALLOW_HOSTS`` (comma-separated ``host`` or ``host:port``);
listens on ``0.0.0.0:8888`` unless ``VERIDIAN_PROXY_PORT`` says otherwise. No third-party deps so
it runs in any image that has Python, including a brick's own ``isolation.image``.
"""

from __future__ import annotations

import os
import select
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

LISTEN_HOST = "0.0.0.0"
DEFAULT_PORT = 8888
_IDLE = "\r\n\r\n"


def parse_allowlist(raw: str) -> set[tuple[str, int | None]]:
    """``"a.com, b.com:8443"`` -> ``{("a.com", None), ("b.com", 8443)}``. A bare host allows any
    port; ``host:port`` pins the port."""
    out: set[tuple[str, int | None]] = set()
    for entry in raw.split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        if ":" in entry:
            host, _, port = entry.rpartition(":")
            out.add((host, int(port)))
        else:
            out.add((entry, None))
    return out


def host_allowed(allowlist: set[tuple[str, int | None]], host: str, port: int) -> bool:
    host = host.lower()
    return (host, port) in allowlist or (host, None) in allowlist


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    allowlist: set[tuple[str, int | None]] = set()

    def log_message(self, fmt: str, *args) -> None:  # noqa: D401 - quieten default logging
        sys.stderr.write("egress-proxy: " + (fmt % args) + "\n")

    def _refuse(self, host: str, port: int) -> None:
        self.log_message("DENY %s:%s (not in allowlist)", host, port)
        body = f"egress to {host}:{port} is not on this brick's allow_hosts".encode()
        self.send_response(403)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self) -> None:  # noqa: N802 - required name
        host, _, port_s = self.path.partition(":")
        port = int(port_s or 443)
        if not host_allowed(self.allowlist, host, port):
            self._refuse(host, port)
            return
        try:
            upstream = socket.create_connection((host, port), timeout=10)
        except OSError as exc:
            self.send_error(502, f"cannot reach {host}:{port}: {exc}")
            return
        self.log_message("ALLOW %s:%s", host, port)
        self.send_response(200, "Connection Established")
        self.end_headers()
        _tunnel(self.connection, upstream)

    def _proxy_plain(self) -> None:
        parts = urlsplit(self.path)
        host = parts.hostname or ""
        port = parts.port or 80
        if not host_allowed(self.allowlist, host, port):
            self._refuse(host, port)
            return
        self.send_error(501, "plain-HTTP proxying is not implemented; use HTTPS (CONNECT)")

    do_GET = _proxy_plain
    do_POST = _proxy_plain
    do_PUT = _proxy_plain
    do_DELETE = _proxy_plain
    do_HEAD = _proxy_plain
    do_PATCH = _proxy_plain


def _tunnel(a: socket.socket, b: socket.socket) -> None:
    a.setblocking(False)
    b.setblocking(False)
    try:
        while True:
            r, _, x = select.select([a, b], [], [a, b], 30)
            if x or not r:
                break
            for src in r:
                dst = b if src is a else a
                data = src.recv(65536)
                if not data:
                    return
                dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def main() -> int:
    raw = os.environ.get("VERIDIAN_ALLOW_HOSTS", "")
    _Handler.allowlist = parse_allowlist(raw)
    port = int(os.environ.get("VERIDIAN_PROXY_PORT", DEFAULT_PORT))
    if not _Handler.allowlist:
        sys.stderr.write("egress-proxy: refusing to start with an empty allowlist\n")
        return 2
    server = ThreadingHTTPServer((LISTEN_HOST, port), _Handler)
    sys.stderr.write(
        f"egress-proxy: listening on {LISTEN_HOST}:{port}; allow={sorted(_Handler.allowlist)}\n"
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
