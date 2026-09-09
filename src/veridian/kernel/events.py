"""An async pub/sub bus with typed events.

Subscribers register against an exact event type or a trailing-``*`` glob (``brick.*``). Delivery
is fire-and-forget on the running loop; a slow or failing subscriber never blocks the publisher or
another subscriber.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# Kernel event types (not exhaustive; bricks emit their own namespaced types via host.event.emit).
KERNEL_READY = "kernel.ready"
KERNEL_STOPPING = "kernel.stopping"
BRICK_STARTING = "brick.starting"
BRICK_READY = "brick.ready"
BRICK_STDERR = "brick.stderr"
BRICK_MALFORMED_LINE = "brick.malformed_line"
BRICK_CRASHED = "brick.crashed"
BRICK_RESTARTING = "brick.restarting"
BRICK_RESTART_EXHAUSTED = "brick.restart_exhausted"
BRICK_STOPPED = "brick.stopped"
CONTRACT_CALL = "contract.call"
CONTRACT_CALL_FAILED = "contract.call_failed"
PERMISSION_DENIED = "permission.denied"


@dataclass(frozen=True)
class Event:
    type: str
    source: str = "kernel"
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[tuple[str, Handler]] = []
        self._tasks: set[asyncio.Task[Any]] = set()
        self._history: list[Event] = []
        self._history_limit = 1000

    def subscribe(self, pattern: str, handler: Handler) -> Callable[[], None]:
        entry = (pattern, handler)
        self._subs.append(entry)

        def _unsub() -> None:
            if entry in self._subs:
                self._subs.remove(entry)

        return _unsub

    def _matches(self, pattern: str, event_type: str) -> bool:
        if pattern == "*" or pattern == event_type:
            return True
        if pattern.endswith(".*"):
            return event_type.split(".")[0] == pattern[:-2]
        return False

    def emit(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]
        for pattern, handler in list(self._subs):
            if not self._matches(pattern, event.type):
                continue
            try:
                result = handler(event)
            except Exception:  # noqa: BLE001 - a bad subscriber must not break emit
                continue
            if asyncio.iscoroutine(result):
                task = asyncio.ensure_future(result)
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    def emit_type(self, type_: str, source: str = "kernel", **payload: Any) -> None:
        self.emit(Event(type=type_, source=source, payload=payload))

    def history(self, pattern: str = "*") -> list[Event]:
        return [e for e in self._history if self._matches(pattern, e.type)]

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
