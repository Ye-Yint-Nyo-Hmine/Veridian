"""A typed convenience client for the ``conversation`` contract.

The SDK is otherwise contract-agnostic, but chat history is meant to be consumed programmatically
by many bricks (every orchestrator, for one), so a small typed wrapper over
``host.contract_call("conversation", ...)`` earns its place. It adds nothing to the wire — the
schema in ``schemas/protocol/conversation.schema.json`` is still the source of truth.
"""

from __future__ import annotations

from typing import Any, Protocol


class _Caller(Protocol):
    async def contract_call(
        self, contract: str, method: str, params: dict[str, Any] | None = None
    ) -> Any: ...


class Conversation:
    """Wraps a :class:`~veridian.sdk.HostProxy` (or any object with ``contract_call``)."""

    def __init__(self, host: _Caller, *, session_id: str | None = None) -> None:
        self._host = host
        self._session = session_id

    def _sid(self, session_id: str | None) -> str:
        sid = session_id or self._session
        if not sid:
            raise ValueError("no session_id given and no default set on the client")
        return sid

    async def append(
        self, message: dict[str, Any], *, session_id: str | None = None, metadata: dict | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"session_id": self._sid(session_id), "message": message}
        if metadata is not None:
            params["metadata"] = metadata
        return await self._host.contract_call("conversation", "append", params)

    async def load(
        self,
        *,
        session_id: str | None = None,
        limit: int | None = None,
        before_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"session_id": self._sid(session_id)}
        if limit is not None:
            params["limit"] = limit
        if before_seq is not None:
            params["before_seq"] = before_seq
        res = await self._host.contract_call("conversation", "load", params)
        return res["messages"]

    async def list_sessions(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        res = await self._host.contract_call("conversation", "list_sessions", params)
        return res["sessions"]

    async def delete(self, *, session_id: str | None = None) -> int:
        res = await self._host.contract_call(
            "conversation", "delete", {"session_id": self._sid(session_id)}
        )
        return res["deleted"]
