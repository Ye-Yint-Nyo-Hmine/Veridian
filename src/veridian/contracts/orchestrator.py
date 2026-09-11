"""The ``orchestrator`` contract: drives the whole agent loop. It is a brick, so replacing it
replaces the agent architecture."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="orchestrator",
    version="1.0",
    schema_file="protocol/orchestrator.schema.json",
    methods=_methods(
        MethodSpec("run", streaming=True),
        # Optional, additive: compress the working context the orchestrator holds for a session.
        # A stack whose orchestrator does not implement it returns method_not_found; callers
        # degrade rather than error. No wire-version bump.
        MethodSpec("compact"),
    ),
)
