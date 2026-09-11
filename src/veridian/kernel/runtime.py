"""The kernel.

It owns process lifecycle, the protocol, permissions, config, and events. It owns no agent logic.
It knows contract names only as opaque strings to route by; it has no concept of a model, a
provider, an inference strategy, or an agent shape. Keeping it that way is the whole point — do
not add anything here that a specific brick would care about.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from veridian.contracts import CONTRACTS, SchemaValidationError
from veridian.contracts.errors import (
    CONTRACT_NOT_BOUND,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    ProtocolError,
)
from veridian.contracts.host import validate_params as validate_host_params
from veridian.kernel.config import ResolvedStack, load_stack
from veridian.kernel.events import (
    CONTRACT_CALL,
    CONTRACT_CALL_CANCELLED,
    CONTRACT_CALL_FAILED,
    KERNEL_READY,
    KERNEL_STOPPING,
    PERMISSION_DENIED,
    EventBus,
)
from veridian.kernel.lifecycle import BrickSupervisor, RestartPolicy
from veridian.plugin_runtime.registry import BrickHandle, PluginRegistry
from veridian.security.capabilities import covers, granted_by_any
from veridian.security.permissions import EffectiveCapabilities

# Contracts whose bricks should come up last, because they drive the others.
_START_LAST = ("orchestrator",)


class Kernel:
    def __init__(
        self,
        stack: ResolvedStack,
        *,
        workspace_root: Path | None = None,
        validate_wire: bool = True,
        restart_policy: RestartPolicy | None = None,
    ) -> None:
        self.stack = stack
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.validate_wire = validate_wire
        self.events = EventBus()
        self.registry = PluginRegistry()
        self._restart_policy = restart_policy or RestartPolicy()
        self._supervisors: list[BrickSupervisor] = []
        self._effective: dict[str, list[str]] = {}
        # Capabilities the host asks to withhold from every brick on top of policy (the
        # interactive session's mode uses this). Opaque strings — the kernel never interprets
        # what they mean, only that a withheld capability is dropped from every brick's
        # effective set and cannot be re-granted dynamically.
        self._restricted: set[str] = set()
        self._started = False

    # -- construction helpers --------------------------------------------------------

    @classmethod
    def from_stack_file(
        cls, path: Path | str, *, workspace_root: Path | None = None, **kw: Any
    ) -> "Kernel":
        resolved = load_stack(Path(path))
        return cls(resolved, workspace_root=workspace_root, **kw)

    # -- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        if self._started:
            return
        order = sorted(
            self.stack.active(),
            key=lambda b: (b.contract in _START_LAST, b.contract),
        )
        for binding in order:
            manifest = binding.manifest
            effective = self._compute_effective(manifest.name, manifest.requires)
            self._effective[manifest.name] = effective
            supervisor = BrickSupervisor(
                binding=binding,
                workspace_root=self.workspace_root,
                effective_capabilities=effective,
                bus=self.events,
                router=self._route_host_call,
                restart_policy=self._restart_policy,
            )
            supervisor.on_rebind = self._make_rebind(binding.contract)
            handle = await supervisor.start()
            self.registry.bind(binding.contract, handle)
            self._supervisors.append(supervisor)

        self._started = True
        self.events.emit_type(
            KERNEL_READY,
            bound=self.registry.bound_contracts(),
            workspace_root=str(self.workspace_root),
        )

    def _make_rebind(self, contract: str):
        async def _rebind(handle: BrickHandle) -> None:
            self.registry.bind(contract, handle)

        return _rebind

    async def rebind(self, contract: str, *, config: dict[str, Any]) -> None:
        """Restart the brick bound to ``contract`` with ``config`` merged into its binding config,
        through a fresh supervisor.

        The new supervisor is started *before* the old one is touched: if it fails to come up this
        raises with the previous brick still bound and serving. Only on a clean start is the old
        brick stopped, unbound, and replaced. The interactive ``/model`` switch relies on this —
        a failed switch must leave the previous model working.
        """
        if not self._started:
            raise ProtocolError(CONTRACT_NOT_BOUND, "kernel not started")
        old = next((s for s in self._supervisors if s.contract == contract), None)
        if old is None:
            raise ProtocolError(CONTRACT_NOT_BOUND, f"no brick bound to contract {contract!r}")

        new_binding = dataclasses.replace(
            old.binding, config={**old.binding.config, **config}
        )
        manifest = new_binding.manifest
        effective = self._compute_effective(manifest.name, manifest.requires)
        new_sup = BrickSupervisor(
            binding=new_binding,
            workspace_root=self.workspace_root,
            effective_capabilities=effective,
            bus=self.events,
            router=self._route_host_call,
            restart_policy=self._restart_policy,
        )
        new_sup.on_rebind = self._make_rebind(contract)
        handle = await new_sup.start()  # BrickStartError / ProtocolError here => nothing swapped

        await old.stop()
        self._supervisors[self._supervisors.index(old)] = new_sup
        self.registry.bind(contract, handle)
        self._effective[manifest.name] = effective
        for i, b in enumerate(self.stack.bindings):
            if b.contract == contract:
                self.stack.bindings[i] = new_binding
                break

    async def stop(self) -> None:
        if not self._started:
            return
        self.events.emit_type(KERNEL_STOPPING)
        for supervisor in reversed(self._supervisors):
            await supervisor.stop()
        self.registry.unbind_all()
        await self.events.drain()
        self._started = False

    async def __aenter__(self) -> "Kernel":
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # -- kernel-originated calls ------------------------------------------------------

    async def call(
        self, contract: str, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = 60.0
    ) -> Any:
        handle = self._require_bound(contract)
        payload = params or {}
        self._maybe_validate_params(contract, method, payload)
        result = await handle.endpoint.call(f"{contract}.{method}", payload, timeout=timeout)
        self._maybe_validate_result(contract, method, result)
        return result

    async def call_stream(
        self, contract: str, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = 300.0
    ):
        handle = self._require_bound(contract)
        payload = params or {}
        self._maybe_validate_params(contract, method, payload)
        return await handle.endpoint.call_stream(f"{contract}.{method}", payload, timeout=timeout)

    def _require_bound(self, contract: str) -> BrickHandle:
        handle = self.registry.resolve(contract)
        if handle is None:
            raise ProtocolError(CONTRACT_NOT_BOUND, f"no brick bound to contract {contract!r}")
        return handle

    # -- host-service router (brick -> kernel) --------------------------------------

    async def _route_host_call(
        self, caller: str, caller_caps: list[str], method: str, params: dict[str, Any]
    ) -> Any:
        if method == "host.log":
            self._on_host_log(caller, params)
            return None
        if method == "host.event.emit":
            self._validate_host(method, params)
            self.events.emit_type(params["type"], source=caller, payload=params.get("payload"))
            return {}
        if method == "host.permission.request":
            self._validate_host(method, params)
            return {"granted": self._grant_dynamic(caller, params["capability"])}
        if method == "host.contract.call":
            self._validate_host(method, params)
            return await self._forward_contract_call(caller, caller_caps, params)
        raise ProtocolError(METHOD_NOT_FOUND, f"unknown host service {method!r}")

    def _validate_host(self, method: str, params: dict[str, Any]) -> None:
        try:
            validate_host_params(method, params)
        except SchemaValidationError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc), exc.as_error_data()) from exc

    def _on_host_log(self, caller: str, params: dict[str, Any]) -> None:
        try:
            validate_host_params("host.log", params)
        except SchemaValidationError:
            return
        self.events.emit_type(
            "brick.log",
            source=caller,
            level=params.get("level", "info"),
            message=params.get("message", ""),
            fields=params.get("fields", {}),
        )

    def _compute_effective(self, name: str, requires: list[str]) -> list[str]:
        """A brick's effective capabilities: policy (manifest ∩ grant − deny), then minus
        anything the host has asked to withhold via :meth:`restrict_capabilities`."""
        allowed = self.stack.policy.effective_capabilities(name, requires)
        allowed = {c for c in allowed if not any(covers(w, c) for w in self._restricted)}
        return sorted(allowed)

    def restrict_capabilities(self, withheld) -> None:
        """Withhold ``withheld`` from every brick, on top of policy, and forbid re-granting them
        dynamically. Replaces any previous restriction (pass an empty iterable to clear it).

        Safe before or after :meth:`start`: called before, the set is applied as each brick comes
        up; called after, every running brick's effective set and its supervisor are updated in
        place. Capabilities are opaque strings here — the kernel does not know what they gate.
        """
        self._restricted = set(withheld)
        if not self._started:
            return
        by_name = {b.manifest.name: b.manifest for b in self.stack.active()}
        for name in list(self._effective):
            manifest = by_name.get(name)
            requires = manifest.requires if manifest else []
            self._effective[name] = self._compute_effective(name, requires)
            for sup in self._supervisors:
                if sup.name == name:
                    sup.effective_capabilities = self._effective[name]

    def _grant_dynamic(self, caller: str, capability: str) -> bool:
        if any(covers(w, capability) for w in self._restricted):
            return False
        policy = self.stack.policy
        granted = policy.granted_to(caller)
        denied = policy.denied_to(caller)
        ok = granted_by_any(granted, capability) and not granted_by_any(denied, capability)
        if ok and capability not in self._effective.get(caller, []):
            self._effective.setdefault(caller, []).append(capability)
            for sup in self._supervisors:
                if sup.name == caller:
                    sup.effective_capabilities = self._effective[caller]
        return ok

    async def _forward_contract_call(
        self, caller: str, caller_caps: list[str], params: dict[str, Any]
    ) -> Any:
        contract = params["contract"]
        method = params["method"]
        inner = params["params"]

        caps = EffectiveCapabilities(caller, set(caller_caps))
        try:
            caps.require_contract(contract, method)
        except ProtocolError:
            self.events.emit_type(
                PERMISSION_DENIED, source=caller, contract=contract, method=method
            )
            raise

        self.events.emit_type(CONTRACT_CALL, source=caller, contract=contract, method=method)
        target = self.registry.resolve(contract)
        if target is None:
            raise ProtocolError(CONTRACT_NOT_BOUND, f"no brick bound to contract {contract!r}")

        self._maybe_validate_params(contract, method, inner)
        try:
            result = await target.endpoint.call(f"{contract}.{method}", inner, timeout=120.0)
        except ProtocolError as exc:
            self.events.emit_type(
                CONTRACT_CALL_FAILED, source=caller, contract=contract, method=method, code=exc.code
            )
            raise
        except asyncio.CancelledError:
            # The caller's own request was cancelled while this forwarded call was in flight.
            # ``Endpoint.call`` has already sent ``$/cancel`` on to the target brick; record that
            # the cancellation cascaded one hop further down the chain.
            self.events.emit_type(
                CONTRACT_CALL_CANCELLED, source=caller, contract=contract, method=method
            )
            raise
        self._maybe_validate_result(contract, method, result)
        return {"result": result}

    # -- optional generic wire validation -----------------------------------------

    def _maybe_validate_params(self, contract: str, method: str, payload: Any) -> None:
        if not self.validate_wire:
            return
        spec = CONTRACTS.get(contract)
        if spec is None or method not in spec.methods:
            return
        try:
            spec.validate_params(method, payload)
        except SchemaValidationError as exc:
            raise ProtocolError(INVALID_PARAMS, str(exc), exc.as_error_data()) from exc

    def _maybe_validate_result(self, contract: str, method: str, payload: Any) -> None:
        if not self.validate_wire:
            return
        spec = CONTRACTS.get(contract)
        if spec is None or method not in spec.methods:
            return
        try:
            spec.validate_result(method, payload)
        except SchemaValidationError as exc:
            raise ProtocolError(
                INVALID_PARAMS, f"brick {contract!r} returned an invalid result: {exc}", exc.as_error_data()
            ) from exc

    # -- introspection ------------------------------------------------------------

    def bound(self) -> dict[str, str]:
        return self.registry.bound_contracts()

    def effective_capabilities(self, brick: str) -> list[str]:
        return list(self._effective.get(brick, []))

    async def health(self) -> dict[str, bool]:
        return {s.contract: await s.health_check() for s in self._supervisors}
