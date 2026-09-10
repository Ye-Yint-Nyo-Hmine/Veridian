"""JSON-RPC and Veridian error codes. Mirrors docs/specifications/protocol.md section 8."""

from __future__ import annotations

from typing import Any

# Standard JSON-RPC 2.0
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Veridian reserved range (-32000 .. -32099)
PERMISSION_DENIED = -32001
CONTRACT_NOT_BOUND = -32002
UNSUPPORTED_METHOD = -32003
BRICK_INTERNAL_ERROR = -32004
BRICK_UNAVAILABLE = -32005
TIMEOUT = -32006
INVALID_MANIFEST = -32007
PROTOCOL_VERSION_MISMATCH = -32008
REQUEST_CANCELLED = -32009

_NAMES = {
    PARSE_ERROR: "parse_error",
    INVALID_REQUEST: "invalid_request",
    METHOD_NOT_FOUND: "method_not_found",
    INVALID_PARAMS: "invalid_params",
    INTERNAL_ERROR: "internal_error",
    PERMISSION_DENIED: "permission_denied",
    CONTRACT_NOT_BOUND: "contract_not_bound",
    UNSUPPORTED_METHOD: "unsupported_method",
    BRICK_INTERNAL_ERROR: "brick_internal_error",
    BRICK_UNAVAILABLE: "brick_unavailable",
    TIMEOUT: "timeout",
    INVALID_MANIFEST: "invalid_manifest",
    PROTOCOL_VERSION_MISMATCH: "protocol_version_mismatch",
    REQUEST_CANCELLED: "request_cancelled",
}


def name_for(code: int) -> str:
    return _NAMES.get(code, "error")


class ProtocolError(Exception):
    """An error that maps cleanly onto a JSON-RPC error object."""

    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error_object(self) -> dict[str, Any]:
        obj: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            obj["data"] = self.data
        return obj

    @classmethod
    def from_error_object(cls, obj: dict[str, Any]) -> "ProtocolError":
        return cls(int(obj["code"]), str(obj.get("message", "")), obj.get("data"))
