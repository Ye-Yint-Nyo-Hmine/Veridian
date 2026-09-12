"""The ``host`` proxy a brick uses to call back into the kernel."""

from __future__ import annotations

from typing import Any

from veridian.plugin_runtime.ipc import Endpoint


class HostProxy:
    def __init__(self, endpoint: Endpoint) -> None:
        self._ep = endpoint

    async def log(self, message: str, *, level: str = "info", **fields: Any) -> None:
        params: dict[str, Any] = {"level": level, "message": message}
        if fields:
            params["fields"] = fields
        await self._ep.notify("host.log", params)

    async def emit(self, type: str, payload: Any = None) -> None:
        await self._ep.call("host.event.emit", {"type": type, "payload": payload})

    async def request_permission(self, capability: str, *, reason: str | None = None) -> bool:
        params: dict[str, Any] = {"capability": capability}
        if reason:
            params["reason"] = reason
        res = await self._ep.call("host.permission.request", params)
        return bool(res["granted"])

    async def contract_call(
        self, contract: str, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> Any:
        """Route a call to whichever brick the stack binds to ``contract``.

        No brick-side deadline by default. The kernel already bounds this call with the target
        binding's ``call_timeout`` and always answers — with a result, or with ``timeout``
        (-32006) naming the brick that overran — and a second ceiling here could only fire first
        and mask it. That is exactly what used to happen: both sides sat at 120s, so raising the
        stack's timeout changed nothing and the error blamed the caller rather than the brick that
        was slow. If the kernel goes away entirely the transport closes and every pending call
        fails with it, so waiting without a timeout cannot hang. Pass ``timeout`` to impose a
        shorter deadline of your own.
        """
        res = await self._ep.call(
            "host.contract.call",
            {"contract": contract, "method": method, "params": params or {}},
            timeout=timeout,
        )
        return res["result"]
