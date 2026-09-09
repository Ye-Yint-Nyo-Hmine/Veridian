"""The Python brick SDK. Thin by design — protocol semantics live in the schemas and the kernel."""

from __future__ import annotations

from veridian.sdk.brick import Brick, BrickError, RequestContext, rpc, run
from veridian.sdk.host import HostProxy

__all__ = ["Brick", "BrickError", "RequestContext", "rpc", "run", "HostProxy"]
