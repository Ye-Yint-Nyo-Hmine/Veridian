"""Host services a brick may call back into. Not a bindable contract; fixed by the protocol."""

from __future__ import annotations

from veridian.contracts import _schemas

SCHEMA_FILE = "protocol/host.schema.json"
METHODS = ("host.log", "host.event.emit", "host.permission.request", "host.contract.call")
NOTIFICATION_METHODS = ("host.log",)

_STEM = {
    "host.log": "log",
    "host.event.emit": "event_emit",
    "host.permission.request": "permission_request",
    "host.contract.call": "contract_call",
}


def _schema_id() -> str:
    return _schemas.schema_id_for(SCHEMA_FILE)


def validate_params(method: str, payload: object) -> None:
    _schemas.validate(_schema_id(), f"$defs/{_STEM[method]}_params", payload)


def validate_result(method: str, payload: object) -> None:
    if method in NOTIFICATION_METHODS:
        return
    _schemas.validate(_schema_id(), f"$defs/{_STEM[method]}_result", payload)
