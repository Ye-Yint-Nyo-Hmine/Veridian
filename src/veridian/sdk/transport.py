"""A stdio transport for a brick process.

The kernel side wires ``asyncio`` pipes straight into :class:`StdioTransport`. A brick is the other
end of those pipes, but ``sys.stdin`` / ``sys.stdout`` are not asyncio streams on Windows, so this
transport uses a dedicated reader thread feeding an ``asyncio.Queue`` and writes synchronously
under a lock.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import AsyncIterator

from veridian.plugin_runtime.transport import Transport, TransportClosed


class ThreadedStdioTransport(Transport):
    def __init__(self) -> None:
        self._loop = asyncio.get_event_loop()
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._stdout = sys.stdout.buffer
        self._write_lock = threading.Lock()
        self._closed = False
        self._reader = threading.Thread(target=self._read_stdin, name="veridian-sdk-stdin", daemon=True)
        self._reader.start()

    def _read_stdin(self) -> None:
        stream = sys.stdin.buffer
        try:
            for raw in stream:
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    self._loop.call_soon_threadsafe(self._inbox.put_nowait, text)
        except Exception:  # noqa: BLE001 - reader thread must not raise into nothing
            pass
        finally:
            self._loop.call_soon_threadsafe(self._inbox.put_nowait, None)

    async def send(self, message: str) -> None:
        if self._closed:
            raise TransportClosed("stdio closed")
        if "\n" in message:
            raise ValueError("a framed message must not contain a raw newline")
        data = message.encode("utf-8") + b"\n"
        with self._write_lock:
            try:
                self._stdout.write(data)
                self._stdout.flush()
            except (BrokenPipeError, OSError) as exc:
                raise TransportClosed(str(exc)) from exc

    async def lines(self) -> AsyncIterator[str]:
        while True:
            item = await self._inbox.get()
            if item is None:
                self._closed = True
                return
            yield item

    async def close(self) -> None:
        self._closed = True

    @property
    def alive(self) -> bool:
        return not self._closed
