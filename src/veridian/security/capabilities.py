"""Capability strings.

A capability is a colon-delimited string naming something a brick may do. The kernel treats them
as opaque except for the prefix grammar below; it never special-cases a capability by meaning.

    contract:<name>              call any method of a bound contract
    contract:<name>:<method>     call one method of a bound contract
    workspace:read | workspace:write
    process:spawn                start child processes (advisory in Milestone 1 — see SECURITY.md)
    network                      open sockets (advisory in Milestone 1)
    env:<VAR>                    receive environment variable VAR

``*`` is a single-segment wildcard: ``contract:*`` covers every contract, ``contract:memory:*``
every method of ``memory``. A grant is broader-or-equal to a request when every segment matches or
the grant segment is ``*`` and the grant is not longer than the request.
"""

from __future__ import annotations


def segments(cap: str) -> list[str]:
    return cap.split(":")


def covers(grant: str, request: str) -> bool:
    """Does holding ``grant`` authorize ``request``?"""
    g, r = segments(grant), segments(request)
    if len(g) > len(r):
        return False
    for gi, ri in zip(g, r):
        if gi != "*" and gi != ri:
            return False
    # A shorter grant authorizes any more-specific request under it:
    # contract:memory covers contract:memory:search.
    return True


def granted_by_any(grants: set[str], request: str) -> bool:
    return any(covers(g, request) for g in grants)


def contract_capability(contract: str, method: str | None = None) -> str:
    return f"contract:{contract}" if method is None else f"contract:{contract}:{method}"


def is_wellformed(cap: str) -> bool:
    parts = segments(cap)
    if not parts or any(p == "" for p in parts):
        return False
    root = parts[0]
    known_roots = {"contract", "workspace", "process", "network", "env"}
    if root not in known_roots:
        return False
    if root == "network":
        return len(parts) == 1
    if root == "workspace":
        return len(parts) == 2 and parts[1] in {"read", "write", "*"}
    if root == "process":
        return len(parts) == 2 and parts[1] in {"spawn", "*"}
    if root == "env":
        return len(parts) == 2
    if root == "contract":
        return 2 <= len(parts) <= 3
    return False
