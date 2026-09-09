"""The ``sandbox`` contract: process execution with confinement."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="sandbox",
    version="1.0",
    schema_file="protocol/sandbox.schema.json",
    methods=_methods(
        MethodSpec("exec"),
        MethodSpec("spawn"),
        MethodSpec("write"),
        MethodSpec("kill"),
        MethodSpec("reset"),
    ),
)
