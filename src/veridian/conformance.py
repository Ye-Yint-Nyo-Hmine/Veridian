"""The conformance harness.

Point it at a brick directory. It starts the brick, negotiates ``veridian/1.1`` (accepting any
peer that shares the major version), calls every
method of every contract the brick reports from ``plugin.capabilities``, and validates both
directions against the JSON Schemas. It is what lets a stranger's brick be trusted, and what a
future Rust kernel would be validated against.

A method that answers with a *schema-valid error* (e.g. an inference brick with no API key) still
conforms — it spoke the protocol correctly. Only a malformed message, a schema violation, or a
lifecycle breach fails conformance.
"""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from veridian.contracts import (
    CONTRACTS,
    PROTOCOL_VERSION,
    SchemaValidationError,
    is_compatible_protocol,
)
from veridian.contracts import lifecycle as lifecycle_contract
from veridian.contracts._schemas import validate as validate_schema
from veridian.contracts.errors import ProtocolError
from veridian.plugin_runtime.manifest import load_manifest
from veridian.plugin_runtime.process import BrickProcess, base_env, spawn_strategy_for
from veridian.plugin_runtime.registry import cross_check_capabilities, parse_capabilities_result

# Minimal valid example params for every contract method. Kept here, next to the harness, so the
# harness has no dependency on any brick or fixture.
EXAMPLES: dict[str, dict[str, dict[str, Any]]] = {
    "inference": {
        "generate": {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 16},
        "generate_stream": {"messages": [{"role": "user", "content": "ping"}], "max_tokens": 16},
        "models": {},
    },
    "model_provider": {
        "complete": {"messages": [{"role": "user", "content": "ping"}], "model": "any", "max_tokens": 16},
        "embed": {"input": ["ping"], "model": "any"},
    },
    "context": {
        "index": {},
        "retrieve": {"query": "ping", "k": 3},
        "invalidate": {},
    },
    "memory": {
        "write": {"content": "conformance probe", "tags": ["_probe"]},
        "read": {"id": "_probe_missing"},
        "search": {"query": "probe", "k": 3},
        "forget": {"tags": ["_probe"]},
    },
    "conversation": {
        "append": {"session_id": "_probe", "message": {"role": "user", "content": "conformance probe"}},
        "load": {"session_id": "_probe", "limit": 10},
        "list_sessions": {},
        "delete": {"session_id": "_probe_missing"},
    },
    "planner": {
        "plan": {"goal": "do a and b"},
        "next": {"plan_id": "_probe"},
        "is_complete": {"plan_id": "_probe"},
    },
    "sandbox": {
        "exec": {"command": ["python", "-c", "print(1)"], "timeout_ms": 5000},
        "spawn": {"command": ["python", "-c", "pass"]},
        "write": {"path": "_probe.txt", "content": "x"},
        "kill": {"handle": "_probe"},
        "reset": {},
    },
    "tools": {
        "list": {},
        "invoke": {"name": "_probe_unknown_tool", "input": {}},
    },
    "workspace": {
        "read": {"path": "_probe_missing"},
        "write": {"path": "_probe.txt", "content": "x"},
        "list": {},
        "stat": {"path": "."},
    },
    "orchestrator": {
        "run": {"goal": "print hello", "workspace_root": ".", "limits": {"max_iterations": 1}},
    },
}


@dataclass
class MethodResult:
    contract: str
    method: str
    ok: bool
    detail: str = ""
    errored_cleanly: bool = False


@dataclass
class ConformanceReport:
    brick: str
    ok: bool
    negotiated: bool
    results: list[MethodResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        passed = sum(1 for r in self.results if r.ok)
        return f"{self.brick}: {'PASS' if self.ok else 'FAIL'} ({passed}/{len(self.results)} methods)"


async def run_conformance(brick_dir: Path, *, timeout: float = 30.0) -> ConformanceReport:
    brick_dir = Path(brick_dir).resolve()
    manifest = load_manifest(brick_dir)
    report = ConformanceReport(brick=manifest.name, ok=False, negotiated=False)

    tmp = tempfile.TemporaryDirectory(prefix="veridian-conformance-")
    workspace = Path(tmp.name)  # a throwaway workspace, so probe writes never touch the repo
    spawn = spawn_strategy_for(manifest).resolve(
        manifest, workspace_root=workspace, brick_env=base_env(manifest.env_passthrough)
    )
    proc = BrickProcess(
        manifest.name,
        spawn.argv,
        cwd=Path(spawn.cwd),
        env=spawn.env,
        on_malformed=lambda raw: report.problems.append(f"malformed stdout line: {raw[:200]}"),
    )
    endpoint = await proc.start()
    try:
        init = await endpoint.call(
            "plugin.initialize",
            {"protocol_version": PROTOCOL_VERSION, "capabilities": manifest.requires,
             "workspace_root": str(workspace), "config": {}},
            timeout=timeout,
        )
        try:
            lifecycle_contract.validate_result("plugin.initialize", init)
        except SchemaValidationError as exc:
            report.problems.append(f"plugin.initialize result invalid: {exc}")
            return report
        if not is_compatible_protocol(init.get("protocol_version")):
            report.problems.append(f"protocol version mismatch: {init.get('protocol_version')}")
            return report
        if not init.get("ready", False):
            report.problems.append(f"brick not ready: {init.get('detail', '')}")
            return report

        caps = await endpoint.call("plugin.capabilities", {}, timeout=timeout)
        lifecycle_contract.validate_result("plugin.capabilities", caps)
        reported = parse_capabilities_result(caps)
        report.negotiated = True

        try:
            cross_check_capabilities(manifest, reported)
        except ProtocolError as exc:
            report.problems.append(f"manifest/capabilities mismatch: {exc}")

        pong = await endpoint.call("plugin.ping", {"nonce": "cf"}, timeout=timeout)
        if pong.get("nonce") not in (None, "cf"):
            report.problems.append("plugin.ping did not echo the nonce")

        for contract, methods in reported.items():
            spec = CONTRACTS.get(contract)
            if spec is None:
                report.problems.append(f"unknown contract {contract!r} reported")
                continue
            for method in methods:
                report.results.append(
                    await _probe_method(endpoint, spec, contract, method, timeout)
                )

        report.ok = not report.problems and all(r.ok for r in report.results)
        return report
    finally:
        try:
            await endpoint.call("plugin.shutdown", {}, timeout=5.0)
        except ProtocolError:
            pass
        await proc.stop()
        try:
            tmp.cleanup()
        except OSError:
            pass


async def _probe_method(endpoint, spec, contract: str, method: str, timeout: float) -> MethodResult:
    example = EXAMPLES.get(contract, {}).get(method)
    if example is None:
        return MethodResult(contract, method, False, "no conformance example defined")

    try:
        spec.validate_params(method, example)
    except SchemaValidationError as exc:  # pragma: no cover - our own examples are valid
        return MethodResult(contract, method, False, f"harness example invalid: {exc}")

    streaming = spec.methods[method].streaming
    try:
        if streaming:
            stream = await endpoint.call_stream(f"{contract}.{method}", example, timeout=timeout)
            async for delta in stream:
                try:
                    spec.validate_delta(method, delta)
                except SchemaValidationError as exc:
                    return MethodResult(contract, method, False, f"invalid delta: {exc}")
            result = await stream.result()
        else:
            result = await endpoint.call(f"{contract}.{method}", example, timeout=timeout)
    except ProtocolError as exc:
        # A well-formed error is conformant behaviour.
        return MethodResult(contract, method, True, f"clean error: {exc.code} {exc.message}", errored_cleanly=True)
    except asyncio.TimeoutError:
        return MethodResult(contract, method, False, "timed out")

    try:
        spec.validate_result(method, result)
    except SchemaValidationError as exc:
        return MethodResult(contract, method, False, f"invalid result: {exc}")
    return MethodResult(contract, method, True, "ok")


def run_conformance_sync(brick_dir: Path, *, timeout: float = 30.0) -> ConformanceReport:
    return asyncio.run(run_conformance(brick_dir, timeout=timeout))
