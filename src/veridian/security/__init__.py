"""Security: capability strings, operator policy, runtime permission checks, trust assessment.

Effective capabilities = what the manifest declares ∩ what policy grants − what policy denies.
Enforced at the host-service boundary (``host.contract.call`` and friends) and at process spawn
(environment scrubbing, working-directory confinement). OS-level network/filesystem isolation is
NOT provided in Milestone 1 — see SECURITY.md.
"""

from __future__ import annotations

from veridian.security.capabilities import contract_capability, covers, granted_by_any, is_wellformed
from veridian.security.permissions import EffectiveCapabilities
from veridian.security.policy import Policy
from veridian.security.trust import TrustAssessment, TrustLevel, assess

__all__ = [
    "Policy",
    "EffectiveCapabilities",
    "contract_capability",
    "covers",
    "granted_by_any",
    "is_wellformed",
    "TrustLevel",
    "TrustAssessment",
    "assess",
]
