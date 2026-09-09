"""The ``planner`` contract: decompose a goal and drive step selection."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="planner",
    version="1.0",
    schema_file="protocol/planner.schema.json",
    methods=_methods(
        MethodSpec("plan"),
        MethodSpec("next"),
        MethodSpec("is_complete"),
    ),
)
