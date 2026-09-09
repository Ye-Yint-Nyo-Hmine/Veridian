"""The ``model_provider`` contract: the raw adapter for exactly one vendor API."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="model_provider",
    version="1.0",
    schema_file="protocol/model_provider.schema.json",
    methods=_methods(
        MethodSpec("complete"),
        MethodSpec("embed"),
    ),
)
