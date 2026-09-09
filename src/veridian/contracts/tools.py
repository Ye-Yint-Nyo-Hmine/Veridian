"""The ``tools`` contract: a catalog of callable tools."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="tools",
    version="1.0",
    schema_file="protocol/tools.schema.json",
    methods=_methods(
        MethodSpec("list"),
        MethodSpec("invoke"),
    ),
)
