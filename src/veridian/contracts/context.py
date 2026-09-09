"""The ``context`` contract: retrieval over the workspace."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="context",
    version="1.0",
    schema_file="protocol/context.schema.json",
    methods=_methods(
        MethodSpec("index"),
        MethodSpec("retrieve"),
        MethodSpec("invalidate"),
    ),
)
