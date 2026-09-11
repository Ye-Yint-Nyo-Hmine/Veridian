"""The kernel: runtime, per-brick lifecycle, event bus, stack config. Small on purpose and with no
knowledge of any model, provider, inference strategy, or agent shape."""

from __future__ import annotations

from veridian.kernel.config import (
    ResolvedBinding,
    ResolvedStack,
    find_repo_root,
    load_stack,
    resolve_stack_ref,
)
from veridian.kernel.errors import BrickStartError, KernelError, StackConfigError
from veridian.kernel.events import Event, EventBus
from veridian.kernel.lifecycle import BrickSupervisor, RestartPolicy
from veridian.kernel.runtime import Kernel

__all__ = [
    "Kernel",
    "EventBus",
    "Event",
    "BrickSupervisor",
    "RestartPolicy",
    "ResolvedStack",
    "ResolvedBinding",
    "load_stack",
    "resolve_stack_ref",
    "find_repo_root",
    "KernelError",
    "StackConfigError",
    "BrickStartError",
]
