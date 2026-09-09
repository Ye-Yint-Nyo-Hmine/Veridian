"""The set of bindable contracts. The kernel routes ``host.contract.call`` by name against this
table; it holds no knowledge of what any brick does with a call."""

from __future__ import annotations

from veridian.contracts import (
    context,
    inference,
    memory,
    model_provider,
    orchestrator,
    planner,
    sandbox,
    tools,
    workspace,
)
from veridian.contracts._spec import ContractSpec

CONTRACTS: dict[str, ContractSpec] = {
    spec.name: spec
    for spec in (
        inference.SPEC,
        model_provider.SPEC,
        context.SPEC,
        memory.SPEC,
        planner.SPEC,
        sandbox.SPEC,
        tools.SPEC,
        workspace.SPEC,
        orchestrator.SPEC,
    )
}


def get(name: str) -> ContractSpec:
    try:
        return CONTRACTS[name]
    except KeyError:
        raise KeyError(f"unknown contract {name!r}; known: {sorted(CONTRACTS)}") from None


def known_contract(name: str) -> bool:
    return name in CONTRACTS
