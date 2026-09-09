"""Schema loading and validation.

The JSON Schema files under ``schemas/`` are the source of truth for the wire format. This module
loads them into a ``referencing`` registry and hands out ``jsonschema`` validators. Nothing in
Python defines the protocol; it only validates against what the schemas say.
"""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SCHEMA_BASE_URI = "https://veridian.dev/schemas/"
PROTOCOL_VERSION = "veridian/1.0"


class SchemaValidationError(ValueError):
    """A payload failed validation against its JSON Schema."""

    def __init__(self, schema_id: str, pointer: str, detail: str, path: str) -> None:
        super().__init__(f"{schema_id}#/{pointer}: {detail} (at {path or '<root>'})")
        self.schema_id = schema_id
        self.pointer = pointer
        self.detail = detail
        self.path = path

    def as_error_data(self) -> dict[str, str]:
        return {"schema": f"{self.schema_id}#/{self.pointer}", "path": self.path, "detail": self.detail}


def find_schema_dir() -> Path:
    """Locate the canonical ``schemas/`` directory.

    Order: ``$VERIDIAN_SCHEMA_DIR``; then walk up from the current working directory; then walk up
    from this file. First directory containing ``protocol/envelope.schema.json`` wins. Bundling the
    schemas into the wheel is a packaging task tracked in ROADMAP.md.
    """
    env = os.environ.get("VERIDIAN_SCHEMA_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        if not (p / "protocol" / "envelope.schema.json").is_file():
            raise RuntimeError(f"VERIDIAN_SCHEMA_DIR={p} has no protocol/envelope.schema.json")
        return p
    here = Path(__file__).resolve()
    for base in (Path.cwd(), *here.parents):
        cand = base / "schemas"
        if (cand / "protocol" / "envelope.schema.json").is_file():
            return cand
    raise RuntimeError("could not locate the Veridian schemas/ directory; set VERIDIAN_SCHEMA_DIR")


@cache
def schema_dir() -> Path:
    return find_schema_dir()


@cache
def _registry() -> Registry:
    resources: list[tuple[str, Resource]] = []
    for path in sorted(schema_dir().rglob("*.schema.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        uri = doc.get("$id")
        if not uri:
            continue
        resources.append((uri, Resource.from_contents(doc, default_specification=DRAFT202012)))
    return Registry().with_resources(resources)


@cache
def load_schema(filename: str) -> dict[str, Any]:
    """Load a top-level schema document by file name, e.g. ``plugin-manifest.schema.json`` or
    ``protocol/inference.schema.json``."""
    return json.loads((schema_dir() / filename).read_text(encoding="utf-8"))


def schema_id_for(filename: str) -> str:
    return load_schema(filename)["$id"]


@cache
def _validator(ref: str) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator({"$ref": ref}, registry=_registry())


def validate(schema_id: str, pointer: str, instance: Any) -> None:
    """Validate ``instance`` against ``schema_id#/pointer``. Raise :class:`SchemaValidationError`."""
    ref = f"{schema_id}#/{pointer}" if pointer else schema_id
    validator = _validator(ref)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    if errors:
        e = errors[0]
        path = "/".join(str(p) for p in e.absolute_path)
        raise SchemaValidationError(schema_id, pointer, e.message, path)


def validate_document(filename: str, instance: Any) -> None:
    """Validate against a whole schema document (used for the manifest and stack config)."""
    validate(schema_id_for(filename), "", instance)


def all_schema_files() -> list[Path]:
    return sorted(schema_dir().rglob("*.schema.json"))


def check_all_schemas_valid() -> list[str]:
    """Return a list of problems; empty means every schema file is itself a valid draft 2020-12
    schema and its ``$id`` resolves."""
    problems: list[str] = []
    reg = _registry()
    for path in all_schema_files():
        doc = json.loads(path.read_text(encoding="utf-8"))
        if "$id" not in doc:
            problems.append(f"{path.name}: missing $id")
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(doc)
        except jsonschema.SchemaError as exc:  # pragma: no cover - defensive
            problems.append(f"{path.name}: invalid schema: {exc.message}")
        try:
            reg.get_or_retrieve(doc["$id"])
        except Exception as exc:  # pragma: no cover - defensive
            problems.append(f"{path.name}: $id {doc['$id']} not resolvable: {exc}")
    return problems
