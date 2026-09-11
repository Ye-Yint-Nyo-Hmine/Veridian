"""Milestone 2 / A3: egress control for containerised bricks.

Hermetic. Covers the allowlist parser, the ``plan_egress`` command construction, and the registry
refusal of unrestricted egress for a content brick. The "a container really cannot reach an
unlisted host" check is ``tests/integration/test_container_isolation.py`` (marked ``container``).
"""

from __future__ import annotations

import pytest

from veridian.contracts.errors import INVALID_MANIFEST, ProtocolError
from veridian.plugin_runtime.manifest import IsolationSpec, parse_manifest
from veridian.security.egress import PROXY_PORT, plan_egress
from veridian.security.egress_proxy import host_allowed, parse_allowlist


# -- the proxy's own allowlist logic --------------------------------------------------------------


def test_parse_allowlist_host_and_hostport():
    al = parse_allowlist("api.anthropic.com, api.openai.com:443 , ")
    assert al == {("api.anthropic.com", None), ("api.openai.com", 443)}


def test_host_allowed_matches_bare_host_on_any_port():
    al = parse_allowlist("api.anthropic.com")
    assert host_allowed(al, "api.anthropic.com", 443)
    assert host_allowed(al, "API.anthropic.com", 8443)
    assert not host_allowed(al, "evil.example", 443)


def test_host_allowed_pins_port_when_given():
    al = parse_allowlist("api.openai.com:443")
    assert host_allowed(al, "api.openai.com", 443)
    assert not host_allowed(al, "api.openai.com", 80)


def test_allowlist_matching_full_matrix():
    """The property the container test proves end-to-end, pinned hermetically: a listed host is
    allowed, an unlisted one is not, and a near-miss must not slip through. Covers the CONNECT
    target as the proxy sees it -- ``host`` with and without a port."""
    al = parse_allowlist("example.com:443, cdn.example.net")

    # listed host:port -> allowed on that port, denied on any other
    assert host_allowed(al, "example.com", 443)
    assert not host_allowed(al, "example.com", 8443)

    # listed bare host -> allowed on whatever port the CONNECT names
    assert host_allowed(al, "cdn.example.net", 443)
    assert host_allowed(al, "cdn.example.net", 8443)

    # a host that simply is not on the list
    assert not host_allowed(al, "example.org", 443)

    # near-misses: a substring / suffix / prefix of a listed host is a different host
    assert not host_allowed(al, "notexample.com", 443)          # prefix glued on
    assert not host_allowed(al, "example.com.attacker.tld", 443)  # listed host as a label
    assert not host_allowed(al, "evil-cdn.example.net", 443)     # shares the registrable domain
    assert not host_allowed(al, "x.example.com", 443)            # subdomain, not the apex


# -- plan_egress -------------------------------------------------------------------------------------


def test_network_false_is_network_none():
    plan = plan_egress(IsolationSpec(mode="container", image="x", network=False),
                       engine="docker", image="x")
    assert plan.network_args == ("--network", "none")
    assert plan.pre_run == () and plan.post_run == () and plan.env == {}


def test_network_true_no_allowlist_is_bare_bridge():
    plan = plan_egress(IsolationSpec(mode="container", image="x", network=True),
                       engine="docker", image="x")
    assert plan.network_args == ()
    assert plan.pre_run == ()


def test_allowlisted_egress_builds_proxy_and_networks():
    spec = IsolationSpec(mode="container", image="python:3.13-slim", network=True,
                         allow_hosts=("api.anthropic.com:443",))
    plan = plan_egress(spec, engine="docker", image="python:3.13-slim", suffix="deadbeef")

    # brick joins only the internal network
    assert plan.network_args == ("--network", "veridian-egress-int-deadbeef")
    # its HTTP client is pointed at the proxy
    assert plan.env["HTTPS_PROXY"] == f"http://veridian-egress-proxy-deadbeef:{PROXY_PORT}"
    assert plan.env["NO_PROXY"] == "localhost,127.0.0.1"

    joined = [" ".join(c) for c in plan.pre_run]
    assert any("network create --internal veridian-egress-int-deadbeef" in c for c in joined)
    assert any("network create veridian-egress-ext-deadbeef" in c for c in joined)
    # the proxy runs from the brick's own image with the allowlist in its environment
    assert any(
        "run -d --rm --name veridian-egress-proxy-deadbeef" in c
        and "VERIDIAN_ALLOW_HOSTS=api.anthropic.com:443" in c
        and "python:3.13-slim python" in c
        for c in joined
    )
    assert any("network connect veridian-egress-ext-deadbeef veridian-egress-proxy-deadbeef" in c for c in joined)

    teardown = [" ".join(c) for c in plan.post_run]
    assert teardown == [
        "docker rm -f veridian-egress-proxy-deadbeef",
        "docker network rm veridian-egress-int-deadbeef",
        "docker network rm veridian-egress-ext-deadbeef",
    ]
    assert plan.proxied_to == ("api.anthropic.com:443",)


# -- registry / manifest refusal -----------------------------------------------------------------

_BASE = {
    "name": "x/y",
    "version": "0.1.0",
    "protocol": "veridian/1.0",
    "spawn": {"command": ["${python}", "brick.py"]},
}


def _m(tmp_path, contract, **iso):
    data = {**_BASE, "implements": [{"contract": contract, "methods": ["a"]}],
            "isolation": {"mode": "container", "image": "img", **iso}}
    return parse_manifest(data, tmp_path)


def test_unrestricted_egress_refused_for_a_content_brick(tmp_path):
    m = _m(tmp_path, "inference", network=True)  # network=true, no allow_hosts
    with pytest.raises(ProtocolError) as ei:
        m.assert_egress_sane()
    assert ei.value.code == INVALID_MANIFEST
    assert "allow_hosts" in str(ei.value)


def test_allowlisted_egress_is_fine_for_a_content_brick(tmp_path):
    m = _m(tmp_path, "inference", network=True, allow_hosts=["api.anthropic.com:443"])
    m.assert_egress_sane()  # no raise


def test_unrestricted_egress_tolerated_for_a_non_content_brick(tmp_path):
    m = _m(tmp_path, "tools", network=True)
    m.assert_egress_sane()  # tools is not in the content set
