"""Phase 2: the JSON-RPC codec — correlation, timeouts, streaming, malformed-line quarantine."""

from __future__ import annotations

import asyncio

import pytest

from veridian.contracts.errors import TIMEOUT, ProtocolError
from veridian.plugin_runtime.ipc import Endpoint
from tests.plugins.conftest import LoopbackTransport


async def _wire(a_handler=None, b_handler=None) -> tuple[Endpoint, Endpoint, list[str]]:
    ta, tb = LoopbackTransport.pair()
    malformed: list[str] = []
    a = Endpoint(ta, name="A", on_malformed=malformed.append, malformed_threshold=3)
    b = Endpoint(tb, name="B")
    if a_handler:
        a.on_request(a_handler)
    if b_handler:
        b.on_request(b_handler)
    a.start()
    b.start()
    return a, b, malformed


async def test_call_response_correlation():
    async def handler(method, params):
        assert method == "echo.say"
        return {"text": params["text"].upper()}

    a, b, _ = await _wire(b_handler=handler)
    result = await a.call("echo.say", {"text": "hi"}, timeout=2.0)
    assert result == {"text": "HI"}
    await a.aclose()
    await b.aclose()


async def test_concurrent_calls_do_not_cross():
    async def handler(method, params):
        await asyncio.sleep(0.05 if params["n"] == 1 else 0.0)
        return {"n": params["n"]}

    a, b, _ = await _wire(b_handler=handler)
    r1, r2 = await asyncio.gather(
        a.call("x", {"n": 1}, timeout=2.0),
        a.call("x", {"n": 2}, timeout=2.0),
    )
    assert (r1, r2) == ({"n": 1}, {"n": 2})
    await a.aclose()
    await b.aclose()


async def test_error_response_maps_to_protocol_error():
    async def handler(method, params):
        raise ProtocolError(-32001, "nope")

    a, b, _ = await _wire(b_handler=handler)
    with pytest.raises(ProtocolError) as ei:
        await a.call("x", {}, timeout=2.0)
    assert ei.value.code == -32001
    await a.aclose()
    await b.aclose()


async def test_call_timeout():
    async def handler(method, params):
        await asyncio.sleep(5)
        return {}

    a, b, _ = await _wire(b_handler=handler)
    with pytest.raises(ProtocolError) as ei:
        await a.call("x", {}, timeout=0.1)
    assert ei.value.code == TIMEOUT
    await a.aclose()
    await b.aclose()


async def test_streaming_deltas_then_result():
    ta, tb = LoopbackTransport.pair()
    a = Endpoint(ta, name="A")
    a.start()

    stream = await a.call_stream("echo.stream", {"text": "x"}, timeout=2.0)

    # Emulate a compliant streaming brick on the raw peer transport: the first outbound request
    # from A gets id 1.
    await tb.send('{"jsonrpc":"2.0","method":"echo.delta","params":{"request_id":1,"index":0}}')
    await tb.send('{"jsonrpc":"2.0","method":"echo.delta","params":{"request_id":1,"index":1}}')
    await tb.send('{"jsonrpc":"2.0","method":"echo.delta","params":{"request_id":1,"index":2}}')
    await tb.send('{"jsonrpc":"2.0","id":1,"result":{"chunks":3}}')

    seen = [d["index"] async for d in stream]
    assert seen == [0, 1, 2]
    assert await stream.result() == {"chunks": 3}
    await a.aclose()


async def test_orphan_delta_for_unknown_request_is_ignored():
    ta, tb = LoopbackTransport.pair()
    notes: list[tuple[str, dict]] = []
    a = Endpoint(ta, name="A")

    async def note_handler(method, params):
        notes.append((method, params))

    a.on_notification(note_handler)
    a.start()
    await tb.send('{"jsonrpc":"2.0","method":"inference.delta","params":{"request_id":999,"delta":{}}}')
    await asyncio.sleep(0.02)
    # No active stream 999 -> routed to the notification handler, not crashed.
    assert notes == [("inference.delta", {"request_id": 999, "delta": {}})]
    await a.aclose()


async def test_malformed_lines_below_threshold_are_quarantined_not_fatal():
    a, b, malformed = await _wire()
    ta = a._t  # noqa: SLF001 - test reaches into the transport to inject raw bytes
    tb = b._t

    async def handler(method, params):
        return {"ok": True}

    b.on_request(handler)

    await tb.send("not json at all")
    await tb.send("{ still not }")
    await asyncio.sleep(0.02)
    # endpoint A survived; a real call still works
    result = await a.call("x", {}, timeout=2.0)
    assert result == {"ok": True}
    assert len(malformed) == 2
    await a.aclose()
    await b.aclose()


async def test_malformed_flood_marks_peer_crashed():
    a, b, malformed = await _wire()
    for _ in range(5):
        await b._t.send("garbage")  # noqa: SLF001
    await asyncio.sleep(0.05)
    assert a.closed
    with pytest.raises(ProtocolError):
        await a.call("x", {}, timeout=1.0)
    await a.aclose()
    await b.aclose()


async def test_peer_disappearing_fails_pending_calls():
    async def slow(method, params):
        await asyncio.sleep(10)

    a, b, _ = await _wire(b_handler=slow)
    task = asyncio.create_task(a.call("x", {}, timeout=5.0))
    await asyncio.sleep(0.02)
    await b.aclose()
    await b._t.close()  # noqa: SLF001
    with pytest.raises(ProtocolError):
        await task
    await a.aclose()
