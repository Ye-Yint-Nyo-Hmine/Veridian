"""Version ordering and the ``[requires]`` compatibility check.

Two jobs:

* **Order installed versions.** Multiple versions of a brick coexist on disk under
  ``VERIDIAN_HOME/bricks/<name>/<version>/``. An unpinned stack reference resolves to the highest;
  :func:`highest` does that comparison.
* **Enforce ``[requires].veridian``.** A brick manifest may declare the range of Veridian versions
  it works against (``[requires]`` table, ``veridian = ">=0.1,<0.2"``). :func:`check_requires`
  evaluates it against the running version at *resolve* time, so an incompatible brick fails while
  the stack is loading — with both versions named — instead of at spawn.

Deliberately small: a numeric-release comparison (``1.2.0`` → ``(1, 2, 0)``) with pre-release /
build metadata dropped, and a comma-separated specifier grammar covering ``>= > <= < == != ~=``.
No dependency on ``packaging``.
"""

from __future__ import annotations

import re

_RELEASE_RE = re.compile(r"\d+(?:\.\d+)*")


class InvalidVersionSpec(ValueError):
    """A ``[requires]`` specifier string could not be parsed."""


def version_key(version: str) -> tuple[int, ...]:
    """A sortable key for a version string. ``"1.2.0"`` → ``(1, 2, 0)``. Leading ``v`` and any
    pre-release / build suffix (``1.2.0rc1``, ``1.2.0+local``) are ignored for ordering. An
    unparseable version sorts lowest."""
    match = _RELEASE_RE.match(version.strip().lstrip("vV"))
    if not match:
        return (-1,)
    return tuple(int(p) for p in match.group(0).split("."))


def _cmp(a: str, b: str) -> int:
    ka, kb = version_key(a), version_key(b)
    width = max(len(ka), len(kb))
    ka += (0,) * (width - len(ka))
    kb += (0,) * (width - len(kb))
    return (ka > kb) - (ka < kb)


def highest(versions: list[str]) -> str:
    """The highest version by numeric-release order. Raises ``ValueError`` on an empty list."""
    if not versions:
        raise ValueError("no versions to choose from")
    best = versions[0]
    for v in versions[1:]:
        if _cmp(v, best) > 0:
            best = v
    return best


_CLAUSE_RE = re.compile(r"\s*(>=|<=|==|!=|~=|>|<)\s*([0-9][0-9.]*)\s*")


def _satisfies_clause(current: str, op: str, target: str) -> bool:
    c = _cmp(current, target)
    if op == ">=":
        return c >= 0
    if op == "<=":
        return c <= 0
    if op == ">":
        return c > 0
    if op == "<":
        return c < 0
    if op == "==":
        return c == 0
    if op == "!=":
        return c != 0
    if op == "~=":
        # Compatible release: >= target, and same leading components except the last may grow.
        if c < 0:
            return False
        tk = version_key(target)
        if len(tk) < 2:
            raise InvalidVersionSpec(f"~= needs at least major.minor, got {target!r}")
        ck = version_key(current)
        ck += (0,) * (len(tk) - len(ck))
        return ck[: len(tk) - 1] == tk[: len(tk) - 1]
    raise InvalidVersionSpec(f"unknown operator {op!r}")  # pragma: no cover


def satisfies(current: str, spec: str) -> bool:
    """Does ``current`` satisfy every comma-separated clause in ``spec``?
    An empty spec is satisfied by anything."""
    spec = spec.strip()
    if not spec:
        return True
    clauses = [c for c in spec.split(",") if c.strip()]
    parsed: list[tuple[str, str]] = []
    for clause in clauses:
        m = _CLAUSE_RE.fullmatch(clause)
        if not m:
            raise InvalidVersionSpec(f"cannot parse version clause {clause!r} in {spec!r}")
        parsed.append((m.group(1), m.group(2)))
    return all(_satisfies_clause(current, op, target) for op, target in parsed)
