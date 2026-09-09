"""Kernel-level errors. Wire errors live in :mod:`veridian.contracts.errors`."""

from __future__ import annotations


class KernelError(Exception):
    """A kernel operation failed for a reason that is not a single brick's wire error."""


class StackConfigError(KernelError):
    """A stack file is missing, invalid, or references a brick that cannot be resolved."""


class BrickStartError(KernelError):
    """A brick failed to start, initialize, or pass its capability cross-check."""
