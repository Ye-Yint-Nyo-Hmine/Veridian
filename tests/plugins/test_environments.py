"""A1: per-brick dependency isolation — the hermetic parts.

The install path itself (uv / npm over the network) is exercised by
``tests/plugins/test_dependency_isolation.py`` behind the ``install`` marker. Here we only check
the manifest plumbing: fingerprinting, the environment record, and how ``${python}`` resolves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from veridian.plugin_runtime.environments import environment_status, resolve_environment
from veridian.plugin_runtime.manifest import (
    UnresolvedEnvironment,
    dependency_fingerprint,
    load_manifest,
)

_NO_DEPS = """\
name = "t/plain"
version = "0.1.0"
protocol = "veridian/1.0"
runtime = "python"
[spawn]
command = ["${python}", "brick.py"]
[[implements]]
contract = "tools"
methods = ["list"]
"""

_WITH_DEPS = _NO_DEPS + """\
[dependencies]
python = ["six==1.16.0"]
"""


def _brick(tmp_path: Path, toml: str) -> Path:
    (tmp_path / "veridian.toml").write_text(toml, encoding="utf-8")
    (tmp_path / "brick.py").write_text("", encoding="utf-8")
    return tmp_path


def test_fingerprint_is_stable_and_order_independent():
    a = dependency_fingerprint({"python": ["a", "b"], "python_version": "3.13"})
    b = dependency_fingerprint({"python_version": "3.13", "python": ["a", "b"]})
    assert a == b
    assert a != dependency_fingerprint({"python": ["a", "c"]})


def test_plain_brick_needs_no_env_and_uses_kernel_interpreter(tmp_path):
    m = load_manifest(_brick(tmp_path, _NO_DEPS))
    assert not m.needs_isolated_env()
    assert environment_status(m) == "n/a"
    assert m.resolved_interpreter() == sys.executable
    assert resolve_environment(m).action == "skipped"


def test_declared_deps_without_install_is_refused_not_silently_shared(tmp_path):
    m = load_manifest(_brick(tmp_path, _WITH_DEPS))
    assert m.needs_isolated_env()
    assert environment_status(m) == "missing"
    # A declared-but-uninstalled brick must NOT borrow the kernel's interpreter: that is the
    # silent dependency-isolation bypass this path exists to close.
    with pytest.raises(UnresolvedEnvironment) as ei:
        m.resolved_interpreter()
    assert ei.value.status == "missing"
    with pytest.raises(UnresolvedEnvironment):
        m.resolved_command()


def test_matching_env_record_is_used(tmp_path):
    m = load_manifest(_brick(tmp_path, _WITH_DEPS))
    rec = tmp_path / ".veridian" / "environment.json"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({
        "runtime": "python",
        "fingerprint": dependency_fingerprint(m.dependencies),
        "interpreter": sys.executable,  # a real, existing interpreter path
    }), encoding="utf-8")
    assert environment_status(m) == "ok"
    assert m.resolved_interpreter() == sys.executable
    assert m.resolved_command()[0] == sys.executable


def test_stale_fingerprint_is_ignored(tmp_path):
    m = load_manifest(_brick(tmp_path, _WITH_DEPS))
    rec = tmp_path / ".veridian" / "environment.json"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({
        "runtime": "python",
        "fingerprint": "deadbeefdeadbeef",
        "interpreter": sys.executable,
    }), encoding="utf-8")
    assert environment_status(m) == "stale"
    # did not trust the stale venv — and did not quietly fall back to the kernel either
    with pytest.raises(UnresolvedEnvironment) as ei:
        m.resolved_interpreter()
    assert ei.value.status == "stale"


def test_missing_interpreter_path_is_ignored(tmp_path):
    m = load_manifest(_brick(tmp_path, _WITH_DEPS))
    rec = tmp_path / ".veridian" / "environment.json"
    rec.parent.mkdir(parents=True)
    rec.write_text(json.dumps({
        "runtime": "python",
        "fingerprint": dependency_fingerprint(m.dependencies),
        "interpreter": str(tmp_path / "nope" / "python.exe"),
    }), encoding="utf-8")
    assert environment_status(m) == "stale"
    with pytest.raises(UnresolvedEnvironment):
        m.resolved_interpreter()
