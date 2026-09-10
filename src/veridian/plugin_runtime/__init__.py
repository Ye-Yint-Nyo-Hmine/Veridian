"""The plugin runtime: transport, JSON-RPC codec, process supervision, manifest, discovery,
and the contract-to-brick binding table."""

from __future__ import annotations

from veridian.plugin_runtime.ipc import Endpoint, StreamCall
from veridian.plugin_runtime.loader import discover, discover_with_errors, resolve_brick
from veridian.plugin_runtime.manifest import (
    IsolationSpec,
    Manifest,
    load_manifest,
    parse_manifest,
)
from veridian.plugin_runtime.process import (
    BrickProcess,
    ContainerSpawn,
    ContainerUnavailable,
    ProcessSpawn,
    ResolvedSpawn,
    SpawnStrategy,
    base_env,
    detect_container_engine,
    python_command,
    spawn_strategy_for,
)
from veridian.plugin_runtime.registry import (
    BrickHandle,
    PluginRegistry,
    cross_check_capabilities,
    parse_capabilities_result,
)
from veridian.plugin_runtime.transport import StdioTransport, Transport, TransportClosed

__all__ = [
    "Endpoint",
    "StreamCall",
    "Transport",
    "StdioTransport",
    "TransportClosed",
    "BrickProcess",
    "base_env",
    "python_command",
    "SpawnStrategy",
    "ProcessSpawn",
    "ContainerSpawn",
    "ResolvedSpawn",
    "ContainerUnavailable",
    "detect_container_engine",
    "spawn_strategy_for",
    "Manifest",
    "IsolationSpec",
    "load_manifest",
    "parse_manifest",
    "discover",
    "discover_with_errors",
    "resolve_brick",
    "PluginRegistry",
    "BrickHandle",
    "cross_check_capabilities",
    "parse_capabilities_result",
]
