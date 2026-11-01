"""Deterministic JSON Schema validator engine and artifact validator."""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from .digests import canonical_json_bytes, join_path, verify_canonical_digest
from .issues import ContractError, ValidationFailure, fail
from .registry import COMMON_ID, SchemaRegistry
from .semantics import validate_semantics


def _type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    raise ContractError(f"unsupported JSON Schema type: {expected}")


def _valid_format(value: str, name: str) -> bool:
    try:
        if name == "date":
            return dt.date.fromisoformat(value).isoformat() == value
        if name == "date-time":
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.tzinfo is not None
    except ValueError:
        return False
    raise ContractError(f"unsupported JSON Schema format: {name}")


def validate_schema(instance: Any, schema: dict[str, Any], registry: SchemaRegistry, path: str = "$") -> None:
    if "$ref" in schema:
        validate_schema(instance, registry.resolve(schema["$ref"]), registry, path)

    if "allOf" in schema:
        for child in schema["allOf"]:
            validate_schema(instance, child, registry, path)
    if "anyOf" in schema:
        failures = []
        for child in schema["anyOf"]:
            try:
                validate_schema(instance, child, registry, path)
                break
            except ValidationFailure as exc:
                failures.append(str(exc))
        else:
            fail("SCHEMA_ANY_OF", path, f"no anyOf branch matched: {failures}")
    if "oneOf" in schema:
        matched = 0
        for child in schema["oneOf"]:
            try:
                validate_schema(instance, child, registry, path)
                matched += 1
            except ValidationFailure:
                pass
        if matched != 1:
            fail("SCHEMA_ONE_OF", path, f"expected exactly one matching branch; found {matched}")
    if "not" in schema:
        try:
            validate_schema(instance, schema["not"], registry, path)
        except ValidationFailure:
            pass
        else:
            fail("SCHEMA_NOT", path, "instance matched a forbidden schema")

    if "const" in schema and instance != schema["const"]:
        fail("SCHEMA_CONST", path, f"expected {schema['const']!r}; got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        fail("SCHEMA_ENUM", path, f"value {instance!r} is not in {schema['enum']!r}")

    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not isinstance(expected_types, list) or not all(isinstance(item, str) for item in expected_types):
            raise ContractError(f"invalid type declaration at {path}")
        if not any(_type_matches(instance, item) for item in expected_types):
            fail("SCHEMA_TYPE", path, f"expected {expected_types}; got {type(instance).__name__}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                fail("SCHEMA_REQUIRED", join_path(path, key), "required property is missing")
        property_names = schema.get("propertyNames")
        if property_names:
            for key in instance:
                validate_schema(key, property_names, registry, join_path(path, key))
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in instance:
                validate_schema(instance[key], child_schema, registry, join_path(path, key))
        unknown = sorted(set(instance) - set(properties))
        additional = schema.get("additionalProperties", True)
        if additional is False and unknown:
            fail("SCHEMA_UNKNOWN_FIELD", path, f"unknown properties: {unknown}")
        if isinstance(additional, dict):
            for key in unknown:
                validate_schema(instance[key], additional, registry, join_path(path, key))
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            fail("SCHEMA_MIN_PROPERTIES", path, "object has too few properties")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            fail("SCHEMA_MIN_ITEMS", path, f"expected at least {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            fail("SCHEMA_MAX_ITEMS", path, f"expected at most {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            serialized = [canonical_json_bytes(item) for item in instance]
            if len(serialized) != len(set(serialized)):
                fail("SCHEMA_UNIQUE_ITEMS", path, "array items are not unique")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                validate_schema(item, items, registry, join_path(path, index))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            fail("SCHEMA_MIN_LENGTH", path, f"string is shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            fail("SCHEMA_MAX_LENGTH", path, f"string is longer than {schema['maxLength']}")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            fail("SCHEMA_PATTERN", path, f"value does not match {schema['pattern']!r}")
        if "format" in schema and not _valid_format(instance, schema["format"]):
            fail("SCHEMA_FORMAT", path, f"value is not valid {schema['format']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            fail("SCHEMA_MINIMUM", path, f"value is below {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            fail("SCHEMA_MAXIMUM", path, f"value is above {schema['maximum']}")


def validate_artifact(artifact: Any, registry: SchemaRegistry) -> None:
    if not isinstance(artifact, dict):
        fail("ARTIFACT_TYPE", "$", "artifact must be an object")
    contract_id = artifact.get("contract_id")
    if not isinstance(contract_id, str) or contract_id not in registry.by_id or contract_id == COMMON_ID:
        fail("CONTRACT_ID", "$.contract_id", f"unknown artifact contract: {contract_id!r}")
    verify_canonical_digest(artifact)
    validate_schema(artifact, registry.by_id[contract_id], registry)
    validate_semantics(artifact)
    from .active_ingestion_semantics import validate_active_ingestion_semantics
    validate_active_ingestion_semantics(contract_id, artifact, fail)

