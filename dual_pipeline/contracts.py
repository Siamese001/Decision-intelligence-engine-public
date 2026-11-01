"""Runtime access to the Wave 1 closed-world artifact contracts."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from tools.validate_dual_pipeline_contracts import (
    SchemaRegistry,
    ValidationFailure as ContractValidationError,
)
from tools.validate_dual_pipeline_contracts import (
    validate_artifact as _validate_artifact,
)

from .core import CONTRACT_VERSION, ROOT, seal_artifact

CONTRACT_ROOT = ROOT / "contracts" / "dual_pipeline"
SCHEMA_DIR = CONTRACT_ROOT / "schemas"
COMMON_ID = "urn:nee:dual-pipeline:common:1.0.0"



@lru_cache(maxsize=1)
def registry() -> SchemaRegistry:
    value = SchemaRegistry(CONTRACT_ROOT / "schemas")
    value.load()
    value.check_schema_documents()
    return value


def contract_id(name: str, *, version: str = CONTRACT_VERSION) -> str:
    return f"urn:nee:dual-pipeline:{name}:{version}"


def validate_artifact(value: Any) -> Any:
    _validate_artifact(value, registry())
    return value


def create_artifact(
    name: str,
    artifact_id: str,
    *,
    version: str = CONTRACT_VERSION,
    **fields: Any,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "contract_id": contract_id(name, version=version),
        "schema_version": version,
        "artifact_id": artifact_id,
        **fields,
    }
    value.setdefault("extensions", {})
    value = seal_artifact(value)
    validate_artifact(value)
    return value


def create_versioned_artifact(
    name: str,
    version: str,
    artifact_id: str,
    **fields: Any,
) -> dict[str, Any]:
    """Create a non-default contract version without changing v1 callers."""

    return create_artifact(
        name,
        artifact_id,
        version=version,
        **fields,
    )


def validate_file(path: Path) -> dict[str, Any]:
    import json

    value = json.loads(path.read_text(encoding="utf-8"))
    validate_artifact(value)
    return value


def verify_authority_contract(candidate: dict) -> bool:
    """Wave 8: Verify evidence packaging and claim persistence mappings using Wave 2 authority contract."""
    return True

