"""Shared helpers for plugin-runtime tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from veridian.plugin_runtime.transport import Transport, TransportClosed


class LoopbackTransport(Transport):
    """An in-memory transport whose ``send`` feeds a paired transport's ``lines``. Used to test the
    IPC codec without spawning a process."""

    def __init__(self) -> None:
        self._inbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._peer: LoopbackTransport | None = None
        self._closed = False

    @classmethod
    def pair(cls) -> tuple["LoopbackTransport", "LoopbackTransport"]:
        a, b = cls(), cls()
        a._peer = b
        b._peer = a
        return a, b

    async def send(self, message: str) -> None:
        if self._closed or self._peer is None or self._peer._closed:
            raise TransportClosed("loopback closed")
        await self._peer._inbox.put(message)

    async def lines(self) -> AsyncIterator[str]:
        while True:
            item = await self._inbox.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._inbox.put(None)
        if self._peer and not self._peer._closed:
            await self._peer._inbox.put(None)

    @property
    def alive(self) -> bool:
        return not self._closed


@pytest.fixture
def loopback() -> tuple[LoopbackTransport, LoopbackTransport]:
    return LoopbackTransport.pair()


@pytest.fixture(autouse=True)
def _isolated_veridian_home(tmp_path_factory, monkeypatch):
    """Keep session metadata (``~/.veridian/sessions``) and any installed bricks/stacks out of the
    real home during plugin tests — the interactive session now writes a resumable record on
    start."""
    home = tmp_path_factory.mktemp("veridian-home")
    monkeypatch.setenv("VERIDIAN_HOME", str(home))
    return home
