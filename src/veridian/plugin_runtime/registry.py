"""The contract -> brick binding table, and the checks that a brick does not lie about itself or
run against the wrong dependencies.

The registry cross-checks the manifest's declared ``implements`` against what
``plugin.capabilities`` actually returns and refuses the binding on a mismatch. A brick whose
manifest claims a contract method its running code does not report is rejected with
``invalid_manifest`` (-32007) before anything is bound to it.

It also refuses to bind a brick that declares a ``[dependencies]`` table but has no private
environment matching its manifest (never installed, or the recorded environment is stale). Such
a brick would otherwise run against whatever packages the kernel happens to have — the exact
failure per-brick dependency isolation exists to prevent — so the binding is rejected here rather
than silently allowed. (A3 will enforce manifest-declared egress at this same seam.)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from veridian.contracts.errors import INVALID_MANIFEST, ProtocolError
from veridian.plugin_runtime.environments import environment_status
from veridian.plugin_runtime.ipc import Endpoint
from veridian.plugin_runtime.manifest import Manifest
from veridian.plugin_runtime.process import BrickProcess


@dataclass
class BrickHandle:
    manifest: Manifest
    process: BrickProcess
    endpoint: Endpoint
    reported_contracts: dict[str, list[str]] = field(default_factory=dict)
    granted_capabilities: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.manifest.name


def cross_check_capabilities(manifest: Manifest, reported: dict[str, list[str]]) -> None:
    """Raise ``invalid_manifest`` if the manifest declares a contract or method the brick does not
    report from ``plugin.capabilities``."""
    problems: list[str] = []
    for contract, methods in manifest.implements.items():
        if contract not in reported:
            problems.append(f"manifest declares contract {contract!r} but plugin.capabilities omits it")
            continue
        missing = sorted(set(methods) - set(reported[contract]))
        if missing:
            problems.append(
                f"manifest declares {contract} methods {missing} that plugin.capabilities does not report"
            )
    if problems:
        raise ProtocolError(
            INVALID_MANIFEST,
            f"{manifest.name}: brick misrepresents itself: " + "; ".join(problems),
            {"problems": problems},
        )


def check_environment_resolved(manifest: Manifest) -> None:
    """Raise ``invalid_manifest`` if the brick declares ``[dependencies]`` but has no private
    environment matching its manifest. ``environment_status`` returns ``"n/a"`` for a brick with
    no dependency table (always fine) and ``"ok"`` once ``veridian brick install`` has resolved
    one; ``"missing"`` and ``"stale"`` are refused."""
    status = environment_status(manifest)
    if status in ("missing", "stale"):
        detail = "was never installed" if status == "missing" else "no longer matches its manifest"
        raise ProtocolError(
            INVALID_MANIFEST,
            f"{manifest.name}: declares [dependencies] but its private environment {detail} "
            f"({status}); run `veridian brick install {manifest.name}` "
            f"(refusing to fall back to the kernel interpreter)",
            {"brick": manifest.name, "environment": status},
        )


def parse_capabilities_result(result: dict) -> dict[str, list[str]]:
    return {c["name"]: list(c["methods"]) for c in result.get("contracts", [])}


class PluginRegistry:
    def __init__(self) -> None:
        self._by_contract: dict[str, BrickHandle] = {}
        self._by_name: dict[str, BrickHandle] = {}

    def bind(self, contract: str, handle: BrickHandle) -> None:
        if not handle.manifest.declares(contract):
            raise ProtocolError(
                INVALID_MANIFEST,
                f"cannot bind {handle.name} to {contract!r}: its manifest does not implement it",
            )
        check_environment_resolved(handle.manifest)
        cross_check_capabilities(handle.manifest, handle.reported_contracts)
        self._by_contract[contract] = handle
        self._by_name[handle.name] = handle

    def resolve(self, contract: str) -> BrickHandle | None:
        return self._by_contract.get(contract)

    def by_name(self, name: str) -> BrickHandle | None:
        return self._by_name.get(name)

    def bound_contracts(self) -> dict[str, str]:
        return {contract: h.name for contract, h in self._by_contract.items()}

    def handles(self) -> list[BrickHandle]:
        seen: dict[int, BrickHandle] = {}
        for h in self._by_contract.values():
            seen[id(h)] = h
        return list(seen.values())

    def unbind_all(self) -> None:
        self._by_contract.clear()
        self._by_name.clear()
