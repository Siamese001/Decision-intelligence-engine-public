"""Fixture verification, inventory checking, and canonical digest assertion."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .digests import load_json, sha256_file, verify_canonical_digest
from .issues import ContractError, ValidationFailure
from .registry import ROOT, SchemaRegistry
from .validator import validate_artifact


def verify_inventory(contract_root: Path, schema_rows: list[dict[str, str]]) -> dict[str, Any]:
    path = contract_root / "contract_inventory.json"
    value = load_json(path)
    if not isinstance(value, dict):
        raise ContractError("contract inventory must be an object")
    verify_canonical_digest(value)
    if value.get("inventory_version") != "1.0.0":
        raise ContractError("unsupported contract inventory version")
    declared = value.get("schemas")
    if not isinstance(declared, list):
        raise ContractError("contract inventory schemas must be an array")
    expected = sorted(schema_rows, key=lambda row: row["path"])
    actual = sorted(declared, key=lambda row: row.get("path", ""))
    if actual != expected:
        raise ContractError("contract inventory does not match schema paths, identities, versions, and byte digests")
    return {
        "path": path.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(path),
        "canonical_digest": value["canonical_digest"]["value"],
        "schema_count": len(expected),
    }


def validate_fixtures(contract_root: Path, registry: SchemaRegistry) -> dict[str, Any]:
    valid_paths = sorted((contract_root / "fixtures" / "valid").glob("*.json"))
    invalid_paths = sorted((contract_root / "fixtures" / "invalid").glob("*.json"))
    if not valid_paths or not invalid_paths:
        raise ContractError("both valid and invalid fixture sets are required")

    valid_rows: list[dict[str, Any]] = []
    covered_contracts: set[str] = set()
    for path in valid_paths:
        artifact = load_json(path)
        validate_artifact(artifact, registry)
        covered_contracts.add(artifact["contract_id"])
        valid_rows.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "contract_id": artifact["contract_id"],
                "canonical_digest": artifact["canonical_digest"]["value"],
                "sha256": sha256_file(path),
                "status": "PASS",
            }
        )

    from .registry import SUB_OBJECT_SCHEMA_IDS
    required_contracts = set(registry.by_id) - SUB_OBJECT_SCHEMA_IDS
    missing_contracts = sorted(required_contracts - covered_contracts)
    if missing_contracts:
        raise ContractError(f"valid fixtures do not cover contracts: {missing_contracts}")

    invalid_rows: list[dict[str, Any]] = []
    for path in invalid_paths:
        artifact = load_json(path)
        try:
            validate_artifact(artifact, registry)
        except ValidationFailure as exc:
            invalid_rows.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "expected_rejection": "PASS",
                    "issue": exc.issue.as_dict(),
                    "sha256": sha256_file(path),
                }
            )
        else:
            raise ContractError(f"invalid fixture unexpectedly passed: {path}")

    required_negative_prefixes = {
        "architecture-missing-region",
        "bad-digest",
        "copilot-missing-locator",
        "disposition-lane-contamination",
        "entity-naked-fuzzy",
        "eval-measurement-aggregate-mismatch",
        "eval-measurement-duplicate-ordinal",
        "eval-measurement-missing-usage",
        "eval-measurement-sensitive-extension",
        "eval-measurement-token-mismatch",
        "eval-measurement-unknown-field",
        "eval-suite-v3-vacuous",
        "learning-auto-apply",
        "otel-sensitive-content",
        "query-lane-blending",
        "unknown-field",
    }
    present_prefixes = {path.stem for path in invalid_paths}
    missing_negative = sorted(required_negative_prefixes - present_prefixes)
    if missing_negative:
        raise ContractError(f"required negative fixtures are missing: {missing_negative}")

    return {
        "valid_count": len(valid_rows),
        "invalid_count": len(invalid_rows),
        "valid": valid_rows,
        "invalid": invalid_rows,
        "contract_coverage": sorted(covered_contracts),
    }

