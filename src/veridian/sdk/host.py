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
        self, contract: str, method: str, params: dict[str, Any] | None = None, *, timeout: float = 120.0
    ) -> Any:
        res = await self._ep.call(
            "host.contract.call",
            {"contract": contract, "method": method, "params": params or {}},
            timeout=timeout,
        )
        return res["result"]
