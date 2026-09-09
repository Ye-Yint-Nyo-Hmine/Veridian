"""Walk the Milestone 2 verification criteria and report pass/fail on each.

    uv run python scripts/milestone2.py            # every criterion implemented so far
    uv run python scripts/milestone2.py A1 A3      # only the named criteria

Criteria that need Docker, a network install, or a local model are marked and skipped (not
failed) when their prerequisite is absent. Exits non-zero if any selected criterion fails.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PY = sys.executable

SKIP = "SKIP"


def _pytest(*node_ids: str, marker: str | None = None) -> tuple[bool | str, str]:
    cmd = [PY, "-m", "pytest", "-q", "--no-header", *node_ids]
    if marker:
        cmd += ["-m", marker, "-p", "no:cacheprovider"]
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    tail = "\n".join((p.stdout + p.stderr).strip().splitlines()[-3:])
    return p.returncode == 0, tail


# -- A1: per-brick dependency isolation --------------------------------------------

def a1_dependency_isolation():
    if not shutil.which("uv"):
        return SKIP, "uv not on PATH"
    return _pytest("tests/plugins/test_dependency_isolation.py", marker="install")


CRITERIA = [
    ("A1", "Per-brick dependency isolation (conflicting pins coexist)", a1_dependency_isolation),
]


def main(argv: list[str]) -> int:
    wanted = {a.upper() for a in argv} or {n for n, _, _ in CRITERIA}
    rows = []
    all_ok = True
    for num, title, fn in CRITERIA:
        if num not in wanted:
            continue
        print(f"... {num}: {title}")
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"harness error: {exc}"
        rows.append((num, title, ok, detail))
        if ok is not SKIP:
            all_ok &= bool(ok)

    print("\n" + "=" * 78)
    print("MILESTONE 2")
    print("=" * 78)
    for num, title, ok, detail in rows:
        mark = "SKIP" if ok is SKIP else ("PASS" if ok else "FAIL")
        print(f"[{mark}] {num}. {title}")
        if detail:
            print(f"        {detail}")
    print("=" * 78)
    print("ALL SELECTED CRITERIA PASS" if all_ok else "SOME CRITERIA FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
