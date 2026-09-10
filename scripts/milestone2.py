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
    # The enforcement half is hermetic: a brick that declares dependencies with no valid
    # environment is refused at spawn, never silently run against the kernel interpreter.
    ok_enforce, t_enforce = _pytest(
        "tests/kernel/test_env_enforcement.py",
        "tests/plugins/test_environments.py",
    )
    if not shutil.which("uv"):
        return (SKIP if ok_enforce else False), f"uv not on PATH; enforcement: {t_enforce}"
    ok_iso, t_iso = _pytest("tests/plugins/test_dependency_isolation.py", marker="install")
    return ok_enforce and ok_iso, f"enforcement: {t_enforce} | isolation: {t_iso}"


# -- A2: container spawn mode -----------------------------------------------------

def a2_container_spawn():
    # Hermetic half: the docker/podman argv is built correctly and process mode is unchanged.
    ok_argv, t_argv = _pytest("tests/plugins/test_container_spawn.py")
    if not shutil.which("docker") and not shutil.which("podman"):
        return (SKIP if ok_argv else False), f"no container engine; argv construction: {t_argv}"
    ok_e2e, t_e2e = _pytest("tests/integration/test_container_isolation.py", marker="container")
    return ok_argv and ok_e2e, f"argv: {t_argv} | e2e: {t_e2e}"


# -- A3: egress control ---------------------------------------------------------

def a3_egress_control():
    ok_unit, t_unit = _pytest(
        "tests/security/test_egress.py",
        "tests/plugins/test_container_spawn.py::test_container_argv_allowlisted_network_joins_the_internal_net_and_proxy",
    )
    if not shutil.which("docker") and not shutil.which("podman"):
        return (SKIP if ok_unit else False), f"no container engine; allowlist plumbing: {t_unit}"
    ok_e2e, t_e2e = _pytest("tests/integration/test_container_isolation.py", marker="container")
    return ok_unit and ok_e2e, f"plumbing: {t_unit} | e2e: {t_e2e}"


# -- A4: cooperative cancellation ------------------------------------------------

def a4_cancellation():
    # Fully hermetic — no engine, no network, no model — so this criterion never skips.
    # Covers the $/cancel cascade through host.contract.call, the interactive Ctrl-C SEAM, and
    # major-version negotiation (a veridian/1.0 brick still binds; 1.0 peers degrade to timeout).
    return _pytest(
        "tests/kernel/test_cancellation.py",
        "tests/plugins/test_cli_ui.py::test_interactive_ctrl_c_sends_cancel_to_the_live_stream",
    )


# -- A5: local-gated inference tests -------------------------------------------------

def a5_local_gated_inference():
    # The live inference tests must gate on whichever provider is reachable (a local
    # OpenAI-compatible server first, a cloud key only as fallback) rather than on cloud keys
    # alone. With a local server up they must EXECUTE, not skip.
    ok, tail = _pytest(
        "tests/plugins/test_bricks_inference.py::test_live_local_generate",
        "tests/plugins/test_bricks_inference.py::test_live_local_models_lists_the_server",
        "tests/plugins/test_orchestrator.py::test_orchestrator_end_to_end_live",
        marker="live",
    )
    executed = " passed" in tail
    if not executed and "skipped" in tail:
        return SKIP, f"no reachable live provider; live inference tests skipped: {tail}"
    return ok, tail


# -- B1: what already holds by construction (documentation) ------------------------

_PRIVACY_DOC = REPO / "docs" / "security" / "privacy.md"


def b1_holds_by_construction():
    if not _PRIVACY_DOC.is_file():
        return False, "docs/security/privacy.md is missing"
    text = _PRIVACY_DOC.read_text(encoding="utf-8").lower()
    needed = [
        "what already holds by construction",
        "memory bricks are local",
        "context bricks read the local workspace only",
        "the kernel is the only component that holds assembled context",
        "the `inference` / `model_provider` split is the privacy layer",
    ]
    missing = [n for n in needed if n not in text]
    return (not missing), ("ok" if not missing else f"privacy.md missing: {missing}")


# -- B4: the gateway-facing model_provider brick (client side only) ----------------

def b4_gateway_provider():
    return _pytest(
        "tests/plugins/test_gateway_provider.py",
        "tests/security/test_identity_stripping.py::test_gateway_provider_forwards_no_client_identity",
    )


# -- B5: the privacy page (boundary table + the three limits) ----------------------

def b5_privacy_page():
    if not _PRIVACY_DOC.is_file():
        return False, "docs/security/privacy.md is missing"
    text = _PRIVACY_DOC.read_text(encoding="utf-8").lower()
    needed = [
        "persistent vs. ephemeral",  # the boundary table heading
        "assembled prompt",  # a table row
        "version 0 buys unlinkability, not confidentiality",  # limit 1
        "a tee cannot protect a prompt from the model provider",  # limit 2
        "remote attestation is meaningless before a hosted worker",  # limit 3
    ]
    missing = [n for n in needed if n not in text]
    return (not missing), ("ok" if not missing else f"privacy.md missing: {missing}")


# -- B2: a first-class local conversation contract ---------------------------------

def b2_conversation_contract():
    # A conversation survives across two separate kernel runs, is readable only from a local
    # SQLite file, and the orchestrator records turns even when a run fails. Also exercises the
    # new contract through the conformance harness.
    return _pytest(
        "tests/plugins/test_conversation.py",
        "tests/conformance/test_all_bricks.py::test_brick_conforms[conversation/sqlite]",
        "tests/contracts/test_schemas.py",
    )


# -- B3: no client identity forwarded to a provider ---------------------------------

def b3_identity_stripping():
    # Fully hermetic: every inference brick is driven against a local capture server and the
    # bytes that left the process are inspected; a planted leaky adapter must be caught.
    return _pytest("tests/security/test_identity_stripping.py")


CRITERIA = [
    ("A1", "Per-brick dependency isolation (conflicting pins coexist)", a1_dependency_isolation),
    ("A2", "Container spawn mode (isolation.mode=container, network denied)", a2_container_spawn),
    ("A3", "Egress control (allowlisted host reachable, unlisted refused)", a3_egress_control),
    ("A4", "Cooperative cancellation ($/cancel cascades; 1.0 peers degrade to timeout)", a4_cancellation),
    ("A5", "Local-gated inference (live tests run against a reachable local provider)", a5_local_gated_inference),
    ("B1", "What already holds by construction (docs/security/privacy.md)", b1_holds_by_construction),
    ("B2", "Local conversation contract (history survives two runs, local-only)", b2_conversation_contract),
    ("B3", "No client identity forwarded to a provider (planted leaky adapter caught)", b3_identity_stripping),
    ("B4", "Gateway-facing model_provider brick (client side; service stays out of repo)", b4_gateway_provider),
    ("B5", "Privacy page: persistent/ephemeral boundary + the three limits", b5_privacy_page),
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
