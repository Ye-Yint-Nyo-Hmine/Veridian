"""Lifecycle methods every brick implements. Not a bindable contract; fixed by the protocol."""

from __future__ import annotations

from veridian.contracts import _schemas

SCHEMA_FILE = "protocol/lifecycle.schema.json"
METHODS = ("plugin.initialize", "plugin.capabilities", "plugin.ping", "plugin.shutdown")

_STEM = {
    "plugin.initialize": "initialize",
    "plugin.capabilities": "capabilities",
    "plugin.ping": "ping",
    "plugin.shutdown": "shutdown",
}


def _schema_id() -> str:
    return _schemas.schema_id_for(SCHEMA_FILE)


def validate_params(method: str, payload: object) -> None:
    _schemas.validate(_schema_id(), f"$defs/{_STEM[method]}_params", payload)


def validate_result(method: str, payload: object) -> None:
    _schemas.validate(_schema_id(), f"$defs/{_STEM[method]}_result", payload)
