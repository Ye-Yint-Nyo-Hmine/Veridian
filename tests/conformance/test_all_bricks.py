"""Phase 7 / Milestone 1 criterion 7: every brick in bricks/ passes the conformance harness,
communicating only over the protocol. No brick is importable from the kernel.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veridian.conformance import run_conformance
from veridian.plugin_runtime.loader import discover_with_errors

REPO = Path(__file__).resolve().parents[2]
BRICKS = REPO / "bricks"
EXAMPLES = REPO / "examples"

_found, _errors = discover_with_errors(BRICKS)
BRICK_DIRS = sorted(m.directory for m in _found.values())
_ex_found, _ex_errors = discover_with_errors(EXAMPLES)
EXAMPLE_DIRS = sorted(m.directory for m in _ex_found.values())


def test_no_manifest_errors_in_bricks_tree():
    assert _errors == []


@pytest.mark.parametrize("brick_dir", BRICK_DIRS, ids=lambda p: p.relative_to(BRICKS).as_posix())
async def test_brick_conforms(brick_dir):
    await _assert_conforms(brick_dir)


@pytest.mark.parametrize("brick_dir", EXAMPLE_DIRS, ids=lambda p: p.relative_to(EXAMPLES).as_posix())
async def test_example_conforms(brick_dir):
    await _assert_conforms(brick_dir)


async def _assert_conforms(brick_dir):
    report = await run_conformance(brick_dir, timeout=30.0)
    assert report.negotiated, report.problems
    detail = "\n".join(f"{r.contract}.{r.method}: {r.detail}" for r in report.results if not r.ok)
    assert report.ok, f"{report.summary()}\nproblems={report.problems}\n{detail}"


def test_kernel_never_imports_a_brick():
    """Criterion 7, the part that matters: nothing under src/veridian imports from bricks/, and
    the kernel talks to bricks only over the protocol."""
    import re

    pattern = re.compile(r"^\s*(from|import)\s+bricks(\.|\s|$)", re.M)
    offenders = []
    for pkg in ("kernel", "plugin_runtime", "contracts", "security", "sdk"):
        for py in (REPO / "src" / "veridian" / pkg).rglob("*.py"):
            if pattern.search(py.read_text(encoding="utf-8")):
                offenders.append(py.relative_to(REPO).as_posix())
    assert offenders == [], offenders
    # bricks/ has no package marker anywhere
    assert not list((REPO / "bricks").rglob("__init__.py"))


def test_negative_fixture_fails_conformance():
    bad = REPO / "tests" / "fixtures" / "nonconformant" / "context_liar"
    report = run_conformance_sync(bad)
    assert not report.ok


def run_conformance_sync(path):
    from veridian.conformance import run_conformance_sync as _s

    return _s(path)
