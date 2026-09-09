"""Phase 1: the schemas are the source of truth and the Python side agrees with them."""

from __future__ import annotations

import pytest

from veridian.contracts import CONTRACTS, SchemaValidationError, validate
from veridian.contracts._schemas import check_all_schemas_valid
from veridian.contracts.models import Message, TextBlock, ToolUseBlock, Usage, to_wire


def test_every_schema_file_is_valid():
    assert check_all_schemas_valid() == []


def test_every_contract_method_has_params_and_result_defs():
    for spec in CONTRACTS.values():
        doc_defs = _load_defs(spec.schema_file)
        for method, mspec in spec.methods.items():
            assert f"{method}_params" in doc_defs, (spec.name, method)
            assert f"{method}_result" in doc_defs, (spec.name, method)
            if mspec.streaming:
                assert "delta" in doc_defs, spec.name


def _load_defs(schema_file: str) -> dict:
    from veridian.contracts._schemas import load_schema

    return load_schema(schema_file).get("$defs", {})


def test_inference_generate_params_roundtrip():
    spec = CONTRACTS["inference"]
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "model": "some-model",
        "max_tokens": 64,
    }
    spec.validate_params("generate", payload)


def test_inference_generate_result_rejects_missing_field():
    spec = CONTRACTS["inference"]
    bad = {"message": {"role": "assistant", "content": "hi"}, "model": "m", "usage": {"input_tokens": 1, "output_tokens": 1}}
    with pytest.raises(SchemaValidationError):
        spec.validate_result("generate", bad)  # missing stop_reason


def test_common_models_validate_against_common_schema():
    common_id = "https://veridian.dev/schemas/protocol/common.schema.json"
    msg = Message(role="assistant", content=[TextBlock(text="hello"), ToolUseBlock(id="t1", name="grep", input={"q": "x"})])
    validate(common_id, "$defs/Message", to_wire(msg))
    validate(common_id, "$defs/Usage", to_wire(Usage(input_tokens=3, output_tokens=9)))


def test_manifest_schema_rejects_missing_implements():
    with pytest.raises(SchemaValidationError):
        validate_document_helper({"name": "x", "version": "0.1.0", "protocol": "veridian/1.0", "spawn": {"command": ["x"]}})


def validate_document_helper(doc: dict) -> None:
    from veridian.contracts import validate_document

    validate_document("plugin-manifest.schema.json", doc)


def test_stack_config_schema_accepts_string_and_object_bindings():
    from veridian.contracts import validate_document

    validate_document(
        "configuration.schema.json",
        {
            "stack": {"name": "default"},
            "bindings": {
                "inference": "bricks/inference/anthropic",
                "context": {"brick": "bricks/context/default", "config": {"k": 5}},
            },
        },
    )
