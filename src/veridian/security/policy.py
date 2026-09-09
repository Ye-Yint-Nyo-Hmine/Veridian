"""Policy: what the operator's stack file allows, independent of what any brick asks for."""

from __future__ import annotations

from dataclasses import dataclass, field

from veridian.security.capabilities import covers


@dataclass(frozen=True)
class Policy:
    """Global grants/denies plus per-brick overrides, read from the stack file.

    ``deny`` always wins. A brick's effective capabilities are the intersection of what its
    manifest requests and what policy grants it, then minus anything denied.
    """

    grant: frozenset[str] = frozenset()
    deny: frozenset[str] = frozenset()
    per_brick_grant: dict[str, frozenset[str]] = field(default_factory=dict)
    per_brick_deny: dict[str, frozenset[str]] = field(default_factory=dict)

    def granted_to(self, brick: str) -> set[str]:
        return set(self.grant) | set(self.per_brick_grant.get(brick, frozenset()))

    def denied_to(self, brick: str) -> set[str]:
        return set(self.deny) | set(self.per_brick_deny.get(brick, frozenset()))

    def effective_capabilities(self, brick: str, manifest_requires: list[str]) -> set[str]:
        granted = self.granted_to(brick)
        denied = self.denied_to(brick)
        effective: set[str] = set()
        for req in manifest_requires:
            if any(covers(g, req) for g in granted) and not any(covers(d, req) for d in denied):
                effective.add(req)
        return effective

    @classmethod
    def from_config(cls, raw: dict, bindings: dict[str, dict]) -> "Policy":
        base = raw.get("policy", {}) if raw else {}
        per_grant: dict[str, frozenset[str]] = {}
        per_deny: dict[str, frozenset[str]] = {}
        for contract, binding in bindings.items():
            if not isinstance(binding, dict):
                continue
            name = binding.get("brick", contract)
            if binding.get("grant"):
                per_grant[name] = frozenset(binding["grant"])
            if binding.get("deny"):
                per_deny[name] = frozenset(binding["deny"])
        return cls(
            grant=frozenset(base.get("grant", [])),
            deny=frozenset(base.get("deny", [])),
            per_brick_grant=per_grant,
            per_brick_deny=per_deny,
        )
