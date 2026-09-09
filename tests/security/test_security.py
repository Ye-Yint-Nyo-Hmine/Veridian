"""Phase 3: capability matching, policy intersection, effective-capability enforcement."""

from __future__ import annotations

import pytest

from veridian.contracts.errors import PERMISSION_DENIED, ProtocolError
from veridian.security import Policy, assess, covers, is_wellformed
from veridian.security.permissions import EffectiveCapabilities
from veridian.plugin_runtime.manifest import load_manifest
from tests.fixtures import ECHO_ROOT


@pytest.mark.parametrize(
    "grant,request_,expected",
    [
        ("contract:memory", "contract:memory:search", True),
        ("contract:memory:search", "contract:memory:search", True),
        ("contract:memory:search", "contract:memory:write", False),
        ("contract:*", "contract:anything:x", True),
        ("contract:memory", "contract:context:retrieve", False),
        ("workspace:*", "workspace:read", True),
        ("network", "network", True),
        ("contract:memory:search", "contract:memory", False),
    ],
)
def test_covers(grant, request_, expected):
    assert covers(grant, request_) is expected


def test_wellformedness():
    assert is_wellformed("contract:memory:search")
    assert is_wellformed("workspace:read")
    assert not is_wellformed("workspace:delete")
    assert not is_wellformed("banana")
    assert not is_wellformed("contract:")


def test_policy_effective_is_manifest_intersect_grant_minus_deny():
    p = Policy(grant=frozenset({"contract:*", "network"}), deny=frozenset({"network"}))
    eff = p.effective_capabilities("b", ["contract:memory", "network", "workspace:read"])
    # contract:memory granted by contract:*, network denied, workspace:read not granted
    assert eff == {"contract:memory"}


def test_policy_per_brick_grant():
    p = Policy.from_config(
        {"policy": {"grant": []}},
        {"ctx": {"brick": "ctx/x", "grant": ["contract:model_provider"]}},
    )
    eff = p.effective_capabilities("ctx/x", ["contract:model_provider"])
    assert eff == {"contract:model_provider"}


def test_effective_capabilities_require():
    caps = EffectiveCapabilities("b", {"contract:memory"})
    caps.require_contract("memory", "search")  # no raise
    with pytest.raises(ProtocolError) as ei:
        caps.require_contract("context", "retrieve")
    assert ei.value.code == PERMISSION_DENIED


def test_trust_assessment_flags_network():
    m = load_manifest(ECHO_ROOT / "roundtrip")  # requires contract:echo + network
    a = assess(m)
    assert a.level.name == "ELEVATED"
    assert "ELEVATED" in a.advisory
