"""The ``memory`` contract: durable agent memory."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="memory",
    version="1.0",
    schema_file="protocol/memory.schema.json",
    methods=_methods(
        MethodSpec("write"),
        MethodSpec("read"),
        MethodSpec("search"),
        MethodSpec("forget"),
    ),
)
