"""Interactive session modes.

A mode is a CLI-level concept — the kernel never hears the word. It maps to a set of capabilities
the session asks the kernel to *withhold* from every running brick, on top of whatever the stack's
policy already allows:

* ``plan`` — read-only intent. ``workspace:write`` and ``contract:sandbox`` are withheld, so the
  agent can read the tree, retrieve context, and plan, but not run commands.
* ``auto`` — nothing withheld; the stack's full grant is in force.

``/mode`` cycles through :data:`MODES`. ``Kernel.restrict_capabilities`` drops the withheld set
from every brick's effective capabilities and refuses to re-grant it dynamically. Withholding
``contract:sandbox`` is enforced at the kernel's ``host.contract.call`` boundary — in ``plan``
mode the orchestrator genuinely cannot reach the sandbox, so ``run_command`` is never offered.
Withholding ``workspace:write`` narrows the capability the tools brick is told it holds; per
SECURITY.md the filesystem write itself is still confined by the brick to the workspace root
rather than gated per call, so treat that withholding as advisory this milestone.
"""

from __future__ import annotations

MODES: tuple[str, ...] = ("plan", "auto")
DEFAULT_MODE = "plan"

# The honest one-liner about what plan mode actually guarantees this milestone. ``contract:sandbox``
# is enforced at the kernel's host.contract.call boundary; ``workspace:write`` withholding only
# narrows the number the tools brick is told it holds, so it is advisory. Shown in /help and, in
# short form, on the footer's mode line.
PLAN_MODE_NOTE = (
    "plan mode: contract:sandbox is enforced (no command execution); "
    "the workspace:write withholding is advisory this milestone."
)

_WITHHELD: dict[str, frozenset[str]] = {
    "plan": frozenset({"workspace:write", "contract:sandbox"}),
    "auto": frozenset(),
}


def withheld_capabilities(mode: str) -> frozenset[str]:
    """Capabilities the session withholds from every brick while in ``mode``."""
    return _WITHHELD.get(mode, frozenset())


def next_mode(mode: str) -> str:
    """The next mode in the cycle; wraps. An unknown mode cycles to the first."""
    try:
        return MODES[(MODES.index(mode) + 1) % len(MODES)]
    except ValueError:
        return MODES[0]
