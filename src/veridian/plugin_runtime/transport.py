"""Transport: framing only.

A :class:`Transport` moves whole protocol messages as UTF-8 strings, one per call. It knows how a
message is delimited on the wire and nothing about JSON-RPC semantics. ``StdioTransport`` is the
only Milestone 1 implementation; the Unix-socket, named-pipe, and remote transports on the roadmap
subclass this without touching :mod:`veridian.plugin_runtime.ipc` or any caller.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class TransportClosed(Exception):
    """The transport's peer went away."""


class Transport(ABC):
    """Frames messages. One :meth:`send` / one yielded :meth:`lines` item == one protocol message."""

    @abstractmethod
    async def send(self, message: str) -> None:
        """Frame and write a single message. Raises :class:`TransportClosed` if the peer is gone."""

    @abstractmethod
    def lines(self) -> AsyncIterator[str]:
        """Yield inbound messages until the peer closes. Blank / whitespace-only frames are skipped."""

    @abstractmethod
    async def close(self) -> None:
        """Release the transport. Idempotent."""

    @property
    @abstractmethod
    def alive(self) -> bool: ...


class StdioTransport(Transport):
    """NDJSON over a pair of asyncio pipes: read from ``reader``, write to ``writer``.

    For a brick subprocess: ``reader`` is the child's stdout, ``writer`` is the child's stdin.
    ``veridian/1.0`` framing is exactly one JSON value per ``\\n``-terminated line.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False
        self._write_lock = asyncio.Lock()

    async def send(self, message: str) -> None:
        if self._closed or self._writer.is_closing():
            raise TransportClosed("stdio transport is closed")
        if "\n" in message:
            raise ValueError("a framed message must not contain a raw newline")
        async with self._write_lock:
            try:
                self._writer.write(message.encode("utf-8") + b"\n")
                await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError, RuntimeError) as exc:
                raise TransportClosed(str(exc)) from exc

    async def lines(self) -> AsyncIterator[str]:
        while True:
            try:
                raw = await self._reader.readline()
            except (asyncio.IncompleteReadError, ConnectionResetError):
                break
            if raw == b"":  # EOF
                break
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # Non-UTF-8 bytes on stdout are a framing violation; surface a sentinel the
                # codec will quarantine rather than silently dropping.
                yield "�" + raw.hex()
                continue
            text = text.strip()
            if text:
                yield text
        self._closed = True

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if not self._writer.is_closing():
                self._writer.close()
        except Exception:  # pragma: no cover - defensive
            pass

    @property
    def alive(self) -> bool:
        return not self._closed and not self._writer.is_closing()
