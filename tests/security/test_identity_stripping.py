"""Milestone 2 / B3: an inference brick must not forward a stable client identity to a provider.

Version 0 buys *unlinkability*, not confidentiality — the provider reads the prompt, but it must
not be handed a durable handle ("this is the same user as last week"). That is enforceable today
and this is the enforcement: every inference brick is pointed at a local capture server standing
in for the provider API, a real `inference.generate` is driven through the kernel, and the bytes
that actually left the process are inspected.

`tests/fixtures/leaky_inference` is a deliberately planted adapter that *does* leak an identifier;
`test_planted_leaky_adapter_is_caught` asserts the check fails it. If that test ever goes green
without the fixture changing, the check has stopped checking.
"""

from __future__ import annotations

import getpass
import json
import socket
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from veridian.kernel import Kernel, load_stack

REPO = Path(__file__).resolve().parents[2]

# Headers whose very presence signals a per-client handle. Transport/auth headers
# (authorization, x-api-key, user-agent, anthropic-version, ...) are deliberately not here.
_IDENTITY_HEADER_PREFIXES = (
    "x-user",
    "x-client-id",
    "x-client-key",
    "x-device",
    "x-machine-id",
    "x-installation",
    "x-veridian-client",
    "x-request-user",
    "x-end-user",
)

# Body fields that name an end user to the provider.
_IDENTITY_BODY_KEYS = ("user", "user_id", "end_user", "end_user_id", "client_id", "installation_id")


def _local_identifiers() -> set[str]:
    """Strings that would tie a request to *this* machine or account if they appeared on the wire."""
    out = {getpass.getuser(), socket.gethostname(), Path.home().name}
    try:
        out.add(socket.getfqdn())
    except OSError:
        pass
    return {s for s in out if s and len(s) > 2}


def assert_no_client_identity(headers: dict[str, str], body: dict) -> None:
    """Raise ``AssertionError`` if an outbound provider request carries a stable client identity."""
    lower = {k.lower(): v for k, v in headers.items()}

    for name in lower:
        assert not any(name == p or name.startswith(p) for p in _IDENTITY_HEADER_PREFIXES), (
            f"outbound request carries identity header {name!r}"
        )

    for key in _IDENTITY_BODY_KEYS:
        assert key not in body, f"outbound request body carries end-user field {key!r}"
    meta = body.get("metadata")
    if isinstance(meta, dict):
        for key in ("user_id", "user", "client_id", "end_user_id"):
            assert key not in meta, f"outbound request metadata carries {key!r}"

    haystack = " ".join([*lower.keys(), *(str(v) for v in lower.values()), json.dumps(body)]).lower()
    for marker in _local_identifiers():
        assert marker.lower() not in haystack, (
            f"outbound request contains local identifier {marker!r} (machine/account handle)"
        )


# -- the capture server: a stand-in provider that records what reached it ------------------------


class _Capture(BaseHTTPRequestHandler):
    captured: dict = {}

    def log_message(self, *a):  # silence
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}
        type(self).captured = {"path": self.path, "headers": dict(self.headers), "body": body}

        if self.path.rstrip("/").endswith("/messages"):  # Anthropic Messages API
            payload = {
                "id": "msg_capture",
                "type": "message",
                "role": "assistant",
                "model": body.get("model", "capture-model"),
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        else:  # OpenAI chat.completions
            payload = {
                "id": "chatcmpl_capture",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model", "capture-model"),
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def capture_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Capture)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


async def _drive(tmp_path, stack_body: str, contract: str, method: str, params: dict) -> dict:
    p = tmp_path / "stack.toml"
    p.write_text(textwrap.dedent(stack_body), encoding="utf-8")
    k = Kernel(load_stack(p), workspace_root=tmp_path)
    await k.start()
    try:
        await k.call(contract, method, params)
    finally:
        await k.stop()
    return dict(_Capture.captured)


async def _drive_generate(tmp_path, stack_body: str) -> dict:
    return await _drive(
        tmp_path,
        stack_body,
        "inference",
        "generate",
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 16, "model": "capture-model"},
    )


# -- the real bricks: each must reach the provider carrying no client handle ---------------------

_OPENAI_COMPATIBLE = {
    "openai": "bricks/inference/openai",
    "local": "bricks/inference/local",
}


@pytest.mark.parametrize("which", sorted(_OPENAI_COMPATIBLE))
async def test_openai_family_forwards_no_client_identity(tmp_path, monkeypatch, capture_server, which):
    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "VERIDIAN_LOCAL_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    _srv, base = capture_server
    brick = _OPENAI_COMPATIBLE[which]
    captured = await _drive_generate(
        tmp_path,
        f"""
        [stack]
        name = "id"
        [policy]
        grant = ["network"]
        [bindings]
        inference = {{ brick = "{brick}", config = {{ base_url = "{base}/v1", api_key = "test-key", model = "capture-model" }} }}
        """,
    )
    assert captured, "capture server saw no request"
    assert_no_client_identity(captured["headers"], captured["body"])


async def test_anthropic_forwards_no_client_identity(tmp_path, monkeypatch, capture_server):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _srv, base = capture_server
    captured = await _drive_generate(
        tmp_path,
        f"""
        [stack]
        name = "id"
        [policy]
        grant = ["network"]
        [bindings]
        inference = {{ brick = "bricks/inference/anthropic", config = {{ base_url = "{base}", model = "capture-model" }} }}
        """,
    )
    assert captured, "capture server saw no request"
    assert_no_client_identity(captured["headers"], captured["body"])


async def test_gateway_provider_forwards_no_client_identity(tmp_path, monkeypatch, capture_server):
    for var in ("VERIDIAN_GATEWAY_URL", "VERIDIAN_GATEWAY_TOKEN", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    _srv, base = capture_server
    captured = await _drive(
        tmp_path,
        f"""
        [stack]
        name = "id"
        [policy]
        grant = ["network"]
        [bindings]
        model_provider = {{ brick = "bricks/model_provider/gateway", config = {{ base_url = "{base}/v1", token = "gw-token", model = "capture-model" }} }}
        """,
        "model_provider",
        "complete",
        {"messages": [{"role": "user", "content": "hi"}], "model": "capture-model", "max_tokens": 16},
    )
    assert captured, "capture server saw no request"
    assert_no_client_identity(captured["headers"], captured["body"])


# -- the planted leak: the check must catch it -------------------------------------------------


async def test_planted_leaky_adapter_is_caught(tmp_path, monkeypatch, capture_server):
    for var in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    _srv, base = capture_server
    leaky = (REPO / "tests" / "fixtures" / "leaky_inference").as_posix()
    captured = await _drive_generate(
        tmp_path,
        f"""
        [stack]
        name = "id"
        [policy]
        grant = ["network"]
        [bindings]
        inference = {{ brick = "{leaky}", config = {{ base_url = "{base}/v1", api_key = "test-key", model = "capture-model" }} }}
        """,
    )
    assert captured, "capture server saw no request"
    with pytest.raises(AssertionError):
        assert_no_client_identity(captured["headers"], captured["body"])
