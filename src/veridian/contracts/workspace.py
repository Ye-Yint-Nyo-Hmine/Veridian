"""The ``workspace`` contract: file access rooted at the workspace."""

from __future__ import annotations

from veridian.contracts._spec import ContractSpec, MethodSpec, _methods

SPEC = ContractSpec(
    name="workspace",
    version="1.0",
    schema_file="protocol/workspace.schema.json",
    methods=_methods(
        MethodSpec("read"),
        MethodSpec("write"),
        MethodSpec("list"),
        MethodSpec("stat"),
    ),
)
