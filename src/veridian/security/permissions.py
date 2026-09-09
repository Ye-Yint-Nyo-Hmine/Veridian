"""Runtime permission checks at the host-service boundary."""

from __future__ import annotations

from veridian.contracts.errors import PERMISSION_DENIED, ProtocolError
from veridian.security.capabilities import contract_capability, granted_by_any


class EffectiveCapabilities:
    """The frozen set of capabilities a running brick actually holds (manifest ∩ policy − deny)."""

    def __init__(self, brick: str, caps: set[str]) -> None:
        self.brick = brick
        self._caps = frozenset(caps)

    def __contains__(self, cap: str) -> bool:
        return granted_by_any(set(self._caps), cap)

    def as_list(self) -> list[str]:
        return sorted(self._caps)

    def require(self, cap: str, *, detail: str = "") -> None:
        if cap not in self:
            raise ProtocolError(
                PERMISSION_DENIED,
                f"{self.brick} lacks capability {cap!r}" + (f": {detail}" if detail else ""),
                {"brick": self.brick, "capability": cap, "held": self.as_list()},
            )

    def require_contract(self, contract: str, method: str) -> None:
        # A grant of contract:<name> or contract:<name>:<method> both satisfy this.
        if contract_capability(contract, method) in self or contract_capability(contract) in self:
            return
        self.require(contract_capability(contract, method))
