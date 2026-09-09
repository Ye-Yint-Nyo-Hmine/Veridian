"""Trust assessment. Milestone 1 is advisory only.

Veridian does not sandbox a brick at the OS level in Milestone 1 (SECURITY.md says so plainly).
This module classifies how much a brick is asking for, so the CLI can warn, but it is not a
boundary. Container and WASM isolation on the roadmap are.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from veridian.plugin_runtime.manifest import Manifest


class TrustLevel(enum.IntEnum):
    MINIMAL = 0  # only talks to other bricks through the kernel
    STANDARD = 1  # reads/writes the workspace
    ELEVATED = 2  # asks for the network or to spawn processes


@dataclass(frozen=True)
class TrustAssessment:
    level: TrustLevel
    reasons: list[str]

    @property
    def advisory(self) -> str:
        if self.level >= TrustLevel.ELEVATED:
            return (
                "ELEVATED: this brick can reach the network or spawn processes, and Milestone 1 "
                "does not confine either at the OS level. Run it only if you trust its source."
            )
        if self.level == TrustLevel.STANDARD:
            return "STANDARD: this brick reads or writes your workspace files."
        return "MINIMAL: this brick only communicates with other bricks through the kernel."


def assess(manifest: Manifest) -> TrustAssessment:
    reasons: list[str] = []
    level = TrustLevel.MINIMAL
    for cap in manifest.requires:
        if cap == "network":
            level = max(level, TrustLevel.ELEVATED)
            reasons.append("requests network access")
        elif cap.startswith("process:"):
            level = max(level, TrustLevel.ELEVATED)
            reasons.append("requests process spawn")
        elif cap.startswith("workspace:"):
            level = max(level, TrustLevel.STANDARD)
            reasons.append(f"requests {cap}")
    if manifest.env_passthrough:
        reasons.append(f"reads env vars: {', '.join(manifest.env_passthrough)}")
    return TrustAssessment(level=level, reasons=reasons)
