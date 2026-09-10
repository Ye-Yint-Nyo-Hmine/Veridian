"""Contract definitions. Schemas under ``schemas/`` are the source of truth; everything here is
validated against them."""

from __future__ import annotations

from veridian.contracts._schemas import (
    PROTOCOL_VERSION,
    SchemaValidationError,
    is_compatible_protocol,
    protocol_major,
    schema_dir,
    validate,
    validate_document,
)
from veridian.contracts._spec import ContractSpec, MethodSpec
from veridian.contracts.errors import ProtocolError
from veridian.contracts.registry import CONTRACTS, get, known_contract

__all__ = [
    "PROTOCOL_VERSION",
    "is_compatible_protocol",
    "protocol_major",
    "SchemaValidationError",
    "ProtocolError",
    "ContractSpec",
    "MethodSpec",
    "CONTRACTS",
    "get",
    "known_contract",
    "schema_dir",
    "validate",
    "validate_document",
]
