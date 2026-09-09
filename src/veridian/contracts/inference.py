"""The ``inference`` contract: a strategy that produces an assistant turn."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="inference",
    version="1.0",
    schema_file="protocol/inference.schema.json",
    methods=_methods(
        MethodSpec("generate"),
        MethodSpec("generate_stream", streaming=True),
        MethodSpec("models"),
    ),
)
