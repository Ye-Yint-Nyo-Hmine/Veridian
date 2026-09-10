"""Walk the eight Milestone 1 criteria and report pass/fail on each.

    uv run python scripts/milestone1.py            # all eight
    uv run python scripts/milestone1.py 5 7        # only criteria 5 and 7

Exits non-zero if any selected criterion fails. Criteria that need a live provider are marked and
skipped (not failed) when no key is set.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable


def _pytest(*node_ids: str, extra: list[str] | None = None) -> tuple[bool, str]:
    cmd = [PY, "-m", "pytest", "-q", "--no-header", *node_ids, *(extra or [])]
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    tail = "\n".join(p.stdout.strip().splitlines()[-3:])
    return p.returncode == 0, tail


def _cli(*args: str) -> tuple[bool, str]:
    p = subprocess.run([PY, "-m", "veridian.cli.main", *args], cwd=REPO, capture_output=True, text=True)
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-2:])
    return p.returncode == 0, tail


# The evidence for "without touching the kernel" is two-part: the swap test proves one identical
# Kernel code path runs green for every binding, and test_kernel_never_imports_a_brick proves the
# kernel holds no compile-time coupling to any brick it routes to.
_NO_KERNEL_EDIT = "tests/conformance/test_all_bricks.py::test_kernel_never_imports_a_brick"


def c1_swap_inference():
    return _pytest("tests/integration/test_swap.py::test_swap_inference", _NO_KERNEL_EDIT)


def c2_swap_context():
    return _pytest("tests/integration/test_swap.py::test_swap_context", _NO_KERNEL_EDIT)


def c3_swap_sandbox():
    return _pytest("tests/integration/test_swap.py::test_swap_sandbox", _NO_KERNEL_EDIT)


def c4_cross_language():
    return _pytest("tests/plugins/test_typescript_sdk.py",
                   "tests/plugins/test_bricks_sandbox_tools.py::test_typescript_git_brick_alongside_python")


def c5_crash_isolation():
    return _pytest("tests/kernel/test_resilience.py")


def c6_manifest_validation():
    ok_all, t1 = _cli("brick", "validate", "--all")
    ok_neg, t2 = _pytest("tests/plugins/test_manifest_and_loader.py::test_bad_manifest_is_rejected",
                         "tests/conformance/test_all_bricks.py::test_negative_fixture_fails_conformance")
    return ok_all and ok_neg, f"{t1} | negative: {t2}"


def c7_protocol_only():
    ok_conf, t1 = _cli("brick", "conformance", "--all")
    ok_imp, t2 = _pytest("tests/conformance/test_all_bricks.py::test_kernel_never_imports_a_brick")
    return ok_conf and ok_imp, f"conformance: {t1} | {t2}"


def c8_no_model_required():
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    doctor = subprocess.run([PY, "-m", "veridian.cli.main", "doctor"], cwd=REPO, capture_output=True, text=True, env=env)
    suite = subprocess.run([PY, "-m", "pytest", "-q", "-m", "not live and not install"], cwd=REPO, capture_output=True, text=True, env=env)
    tail = "\n".join(suite.stdout.strip().splitlines()[-2:])
    return doctor.returncode == 0 and suite.returncode == 0, f"doctor ok={doctor.returncode == 0} | {tail}"


CRITERIA = [
    ("1", "Swap inference without touching the kernel", c1_swap_inference),
    ("2", "Swap context without touching the kernel", c2_swap_context),
    ("3", "Swap sandbox without touching the kernel", c3_swap_sandbox),
    ("4", "Cross-language bricks (TypeScript beside Python)", c4_cross_language),
    ("5", "Crash isolation (kernel survives every brick failure)", c5_crash_isolation),
    ("6", "Manifest validation (all pass; negatives rejected)", c6_manifest_validation),
    ("7", "Protocol-only communication (conformance + no brick imports)", c7_protocol_only),
    ("8", "No model or provider required (doctor + hermetic suite)", c8_no_model_required),
]


def main(argv: list[str]) -> int:
    wanted = set(argv) or {n for n, _, _ in CRITERIA}
    rows = []
    all_ok = True
    for num, title, fn in CRITERIA:
        if num not in wanted:
            continue
        print(f"... criterion {num}: {title}")
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"harness error: {exc}"
        rows.append((num, title, ok, detail))
        all_ok &= ok

    print("\n" + "=" * 78)
    print("MILESTONE 1")
    print("=" * 78)
    for num, title, ok, detail in rows:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {num}. {title}")
        if detail:
            print(f"        {detail}")
    print("=" * 78)
    print("ALL CRITERIA PASS" if all_ok else "SOME CRITERIA FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
