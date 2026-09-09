"""The data structures that describe a contract. Populated from the schema files, never the
other way round."""

from __future__ import annotations

from dataclasses import dataclass, field

from veridian.contracts import _schemas


@dataclass(frozen=True)
class MethodSpec:
    name: str
    streaming: bool = False

    @property
    def params_pointer(self) -> str:
        return f"$defs/{self.name}_params"

    @property
    def result_pointer(self) -> str:
        return f"$defs/{self.name}_result"

    @property
    def delta_pointer(self) -> str:
        return "$defs/delta"


@dataclass(frozen=True)
class ContractSpec:
    name: str
    version: str
    schema_file: str  # e.g. "protocol/inference.schema.json"
    methods: dict[str, MethodSpec] = field(default_factory=dict)

    @property
    def schema_id(self) -> str:
        return _schemas.schema_id_for(self.schema_file)

    @property
    def method_names(self) -> list[str]:
        return list(self.methods)

    def validate_params(self, method: str, payload: object) -> None:
        _schemas.validate(self.schema_id, self.methods[method].params_pointer, payload)

    def validate_result(self, method: str, payload: object) -> None:
        _schemas.validate(self.schema_id, self.methods[method].result_pointer, payload)

    def validate_delta(self, method: str, payload: object) -> None:
        _schemas.validate(self.schema_id, self.methods[method].delta_pointer, payload)


def _methods(*specs: MethodSpec) -> dict[str, MethodSpec]:
    return {m.name: m for m in specs}
