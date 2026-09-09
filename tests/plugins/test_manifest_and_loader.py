"""Phase 2: manifest parsing/validation and brick discovery."""

from __future__ import annotations

import pytest

from veridian.contracts.errors import INVALID_MANIFEST, ProtocolError
from veridian.plugin_runtime.loader import discover, discover_with_errors, resolve_brick
from veridian.plugin_runtime.manifest import load_manifest
from tests.fixtures import ECHO_ROOT


def test_load_valid_manifest():
    m = load_manifest(ECHO_ROOT / "ok")
    assert m.name == "echo/ok"
    assert m.implements == {"echo": ["say", "stream"]}
    assert m.declares("echo", "say")
    assert not m.declares("echo", "nope")
    assert m.protocol == "veridian/1.0"


def test_bad_manifest_is_rejected():
    with pytest.raises(ProtocolError) as ei:
        load_manifest(ECHO_ROOT / "bad_manifest")
    assert ei.value.code == INVALID_MANIFEST


def test_missing_manifest_is_rejected(tmp_path):
    with pytest.raises(ProtocolError) as ei:
        load_manifest(tmp_path)
    assert ei.value.code == INVALID_MANIFEST


def test_discover_skips_broken_manifest_but_reports_it():
    table, errors = discover_with_errors(ECHO_ROOT)
    assert "echo/ok" in table
    assert "echo/bad-manifest" not in table
    assert any("bad_manifest" in str(p) for p, _ in errors)
    # the tolerant view drops it silently
    assert "echo/bad-manifest" not in discover(ECHO_ROOT)


def test_resolve_brick_by_path():
    m = resolve_brick(str(ECHO_ROOT / "ok"), search_roots=[])
    assert m.name == "echo/ok"


def test_resolve_brick_by_name():
    m = resolve_brick("echo/ok", search_roots=[ECHO_ROOT / "ok"])
    assert m.name == "echo/ok"


def test_resolve_brick_unknown():
    with pytest.raises(FileNotFoundError):
        resolve_brick("nope/nope", search_roots=[ECHO_ROOT / "ok"])
