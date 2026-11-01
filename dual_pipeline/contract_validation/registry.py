"""Schema registry and reference resolution for Wave 1 contracts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .digests import load_json, sha256_file
from .issues import ContractError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
COMMON_ID = "urn:nee:dual-pipeline:common:1.0.0"
SUB_OBJECT_SCHEMA_IDS = frozenset({
    COMMON_ID,
    "urn:nee:dual-pipeline:hitl-option:1.0.0",
    "urn:nee:dual-pipeline:hitl-option:1.1.0",
    "urn:nee:dual-pipeline:hitl-option:1.2.0",
    "urn:nee:dual-pipeline:hook-performance-optimization-receipt:1.0.0",
    "urn:nee:dual-pipeline:evidence-coverage:1.0.0",
    "urn:nee:dual-pipeline:publication-coverage-receipt:1.0.0",
    "urn:nee:dual-pipeline:visual-content:1.0.0",
    "urn:nee:dual-pipeline:agent-autonomy-contract:1.0.0",
    "urn:nee:dual-pipeline:adversarial-judge-evaluation:1.0.0",
})
ARTIFACT_REQUIRED_FIELDS = {
    "contract_id",
    "schema_version",
    "artifact_id",
    "canonical_digest",
    "extensions",
}
SENSITIVE_OTEL_KEY = re.compile(
    r"(?:raw|content|text|question|prompt|answer|claim|statement|transcript|quote|email|name|attendee|owner)",
    re.IGNORECASE,
)


NON_ARTIFACT_SCHEMAS: set[str] = set()


def _validate_single_schema(path: Path, schema_id: str, schema: dict[str, Any]) -> str:
    """Validate dialect and object contracts for a single schema, returning schema_version."""
    if schema.get("$schema") != SCHEMA_DIALECT:
        raise ContractError(f"wrong JSON Schema dialect: {path}")
    if schema_id in SUB_OBJECT_SCHEMA_IDS:
        if "1.2.0" in schema_id:
            return "1.2.0"
        if "1.1.0" in schema_id:
            return "1.1.0"
        return "1.0.0"
    if schema.get("type") != "object":
        raise ContractError(f"artifact schema is not object-typed: {path}")
    if schema.get("additionalProperties") is not False:
        raise ContractError(f"artifact schema is not fail-closed: {path}")
    required = set(schema.get("required", []))
    missing = sorted(ARTIFACT_REQUIRED_FIELDS - required)
    if missing:
        raise ContractError(f"artifact schema missing common required fields {missing}: {path}")
    version = schema.get("properties", {}).get("schema_version", {}).get("const")
    if not isinstance(version, str):
        raise ContractError(f"artifact schema has no fixed schema_version: {path}")
    return version


class SchemaRegistry:
    _CACHE: dict[str, tuple[dict[str, dict[str, Any]], dict[str, Path]]] = {}

    def __init__(self, schema_dir: Path):
        self.schema_dir = Path(schema_dir).resolve()
        self.by_id: dict[str, dict[str, Any]] = {}
        self.path_by_id: dict[str, Path] = {}

    def load(self) -> None:
        key = str(self.schema_dir)
        if key in self._CACHE:
            cached_by_id, cached_path_by_id = self._CACHE[key]
            self.by_id = dict(cached_by_id)
            self.path_by_id = dict(cached_path_by_id)
            return

        paths = [
            p for p in sorted(self.schema_dir.glob("*.schema.json"))
            if p.name not in NON_ARTIFACT_SCHEMAS
        ]
        if not paths:
            raise ContractError(f"no schemas found under {self.schema_dir}")
        for path in paths:
            schema = load_json(path)
            if not isinstance(schema, dict):
                raise ContractError(f"schema must be an object: {path}")
            schema_id = schema.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                raise ContractError(f"schema has no $id: {path}")
            if schema_id in self.by_id:
                raise ContractError(f"duplicate schema $id: {schema_id}")
            self.by_id[schema_id] = schema
            self.path_by_id[schema_id] = path

        self._CACHE[key] = (dict(self.by_id), dict(self.path_by_id))

    def resolve(self, reference: str) -> dict[str, Any]:
        base, marker, fragment = reference.partition("#")
        if base not in self.by_id:
            raise ContractError(f"unresolved schema reference: {reference}")
        value: Any = self.by_id[base]
        if marker and fragment:
            if not fragment.startswith("/"):
                raise ContractError(f"unsupported non-pointer fragment: {reference}")
            for raw_token in fragment[1:].split("/"):
                token = raw_token.replace("~1", "/").replace("~0", "~")
                if not isinstance(value, dict) or token not in value:
                    raise ContractError(f"unresolved JSON pointer: {reference}")
                value = value[token]
        if not isinstance(value, dict):
            raise ContractError(f"schema reference is not an object: {reference}")
        return value

    def check_schema_documents(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for schema_id, schema in sorted(self.by_id.items()):
            path = self.path_by_id[schema_id]
            version = _validate_single_schema(path, schema_id, schema)
            for reference in iter_refs(schema):
                self.resolve(reference)
            rows.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "schema_id": schema_id,
                    "schema_version": version,
                    "sha256": sha256_file(path),
                }
            )
        return rows


def iter_refs(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str):
                yield item
            else:
                yield from iter_refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_refs(item)

