"""Dual-Pipeline Contract Governance MCP Server (Rank 3).

Exposes fail-closed JSON Schema validation, RFC-8259 canonical hashing,
contract inventory verification, and intake package generation as native
Model Context Protocol (MCP) tools.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema
from mcp.server.mcpserver import MCPServer

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

from dual_pipeline.core import (
    artifact_digest,
    canonical_bytes,
    canonical_json,
    sha256_bytes,
    sha256_file,
)

server = MCPServer(
    name="contract-governance",
    instructions=(
        "Dual-Pipeline Contract Governance Server — provides fail-closed JSON Schema validation, "
        "RFC-8259 canonical byte hashing, contract inventory synchronization, and intake package building."
    )
)

CONTRACTS_DIR = WORKSPACE_ROOT / "contracts" / "dual_pipeline"
INVENTORY_PATH = CONTRACTS_DIR / "contract_inventory.json"
SCHEMAS_DIR = CONTRACTS_DIR / "schemas"


def _load_contract_inventory() -> Dict[str, Any]:
    if INVENTORY_PATH.is_file():
        try:
            return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _resolve_schema_path(schema_urn_or_path: str) -> Optional[Path]:
    # Check if directly a path
    p = Path(schema_urn_or_path)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    if p.is_file():
        return p

    # Check in schemas dir
    candidate = SCHEMAS_DIR / schema_urn_or_path
    if candidate.is_file():
        return candidate
    if not schema_urn_or_path.endswith(".json"):
        candidate_json = SCHEMAS_DIR / f"{schema_urn_or_path}.schema.json"
        if candidate_json.is_file():
            return candidate_json

    # Check inventory URNs
    inv = _load_contract_inventory()
    schemas = inv.get("schemas", [])
    if isinstance(schemas, list):
        for item in schemas:
            if item.get("schema_id") == schema_urn_or_path or item.get("path", "").endswith(schema_urn_or_path):
                resolved = WORKSPACE_ROOT / item.get("path", "")
                if resolved.is_file():
                    return resolved

    return None


@server.tool(name="schema_validate_payload")
def schema_validate_payload(schema_urn_or_path: str, payload_json: str) -> str:
    """Validate a JSON payload against a dual-pipeline JSON Schema with fail-closed diagnostics.

    Args:
        schema_urn_or_path: Schema URN, filename (e.g. 'eval-suite.schema.json'), or path.
        payload_json: JSON string to validate.
    """
    schema_file = _resolve_schema_path(schema_urn_or_path)
    if not schema_file or not schema_file.is_file():
        return json.dumps({
            "valid": False,
            "error": f"Schema not found for '{schema_urn_or_path}'. Checked schemas directory and contract inventory."
        })

    try:
        schema_data = json.loads(schema_file.read_text(encoding="utf-8"))
        payload_data = json.loads(payload_json)

        validator = jsonschema.Draft7Validator(schema_data)
        errors = list(validator.iter_errors(payload_data))

        if not errors:
            return json.dumps({
                "valid": True,
                "schema": str(schema_file.relative_to(WORKSPACE_ROOT)),
                "status": "PASS",
            }, indent=2)

        error_details = []
        for err in errors:
            path_str = "/" + "/".join(str(p) for p in err.path)
            error_details.append({
                "jsonpath": path_str or "/",
                "message": err.message,
                "validator": err.validator,
                "validator_value": str(err.validator_value)[:100],
            })

        return json.dumps({
            "valid": False,
            "status": "FAIL_CLOSED",
            "schema": str(schema_file.relative_to(WORKSPACE_ROOT)),
            "error_count": len(error_details),
            "errors": error_details,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"valid": False, "error": f"Validation execution error: {exc}"})


@server.tool(name="rfc8259_canonical_hash")
def rfc8259_canonical_hash(json_payload: str, exclude_fields_csv: str = "canonical_digest") -> str:
    """Compute RFC-8259 canonical representation (sorted keys, compact separators) and SHA-256 digest.

    Args:
        json_payload: JSON string payload.
        exclude_fields_csv: Comma-separated list of top-level field names to exclude before hashing.
    """
    try:
        data = json.loads(json_payload)
        if isinstance(data, dict):
            excludes = [f.strip() for f in exclude_fields_csv.split(",") if f.strip()]
            for f in excludes:
                data.pop(f, None)

        canonical_str = canonical_json(data)
        sha256_hash = sha256_bytes(canonical_bytes(data))

        return json.dumps({
            "sha256": sha256_hash,
            "byte_length": len(canonical_str.encode("utf-8")),
            "canonical_preview": canonical_str[:200] + ("..." if len(canonical_str) > 200 else ""),
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Hashing failed: {exc}"})


@server.tool(name="contract_inventory_verify")
def contract_inventory_verify(schema_name_or_urn: str = "") -> str:
    """Verify that registered contract schemas match their recorded SHA-256 byte hashes in contract_inventory.json.

    Args:
        schema_name_or_urn: Optional specific schema URN or filename to check, or empty to check all.
    """
    inv = _load_contract_inventory()
    if not inv:
        return json.dumps({"error": "contract_inventory.json could not be loaded."})

    schemas = inv.get("schemas", [])
    results: List[Dict[str, Any]] = []
    has_drift = False

    for item in schemas:
        urn = item.get("schema_id", "")
        rel_path = item.get("path", "")
        expected_hash = item.get("sha256", "")

        if schema_name_or_urn and (schema_name_or_urn not in urn and schema_name_or_urn not in rel_path):
            continue

        file_path = WORKSPACE_ROOT / rel_path

        if not file_path.is_file():
            results.append({
                "urn": urn,
                "path": rel_path,
                "status": "FILE_MISSING",
                "expected_hash": expected_hash,
                "actual_hash": None,
            })
            has_drift = True
            continue

        actual_hash = sha256_file(file_path)
        is_match = (actual_hash == expected_hash)
        if not is_match:
            has_drift = True

        results.append({
            "urn": urn,
            "path": rel_path,
            "status": "PASS" if is_match else "HASH_MISMATCH",
            "matches": is_match,
            "actual_hash": actual_hash,
            "expected_hash": expected_hash,
        })

    return json.dumps({
        "status": "ALL_IN_SYNC" if not has_drift else "DRIFT_DETECTED",
        "checked_count": len(results),
        "schemas": results,
    }, indent=2)


@server.tool(name="build_intake_package")
def build_intake_package(
    draft_projection_json: str,
    source_path: str,
    output_dir: str
) -> str:
    """Build a sealed intake package folder ready for 'nee intake-directive-open'.

    Args:
        draft_projection_json: JSON content of Output 03 canonical signal projection.
        source_path: Path to source transcript or export file.
        output_dir: Output directory to write the packaged files.
    """
    try:
        out_p = Path(output_dir)
        if not out_p.is_absolute():
            out_p = WORKSPACE_ROOT / out_p
        out_p.mkdir(parents=True, exist_ok=True)

        proj_data = json.loads(draft_projection_json)
        proj_file = out_p / "signal_projection.json"
        proj_file.write_text(canonical_json(proj_data), encoding="utf-8")

        s_path = Path(source_path)
        if not s_path.is_absolute():
            s_path = WORKSPACE_ROOT / s_path

        src_hash = sha256_file(s_path) if s_path.is_file() else "SOURCE_MISSING"
        proj_hash = sha256_file(proj_file)

        manifest = {
            "package_urn": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "source_path": str(s_path),
            "source_sha256": src_hash,
            "signal_projection_sha256": proj_hash,
            "canonical_digest": "",
        }
        manifest["canonical_digest"] = artifact_digest(manifest)

        manifest_file = out_p / "package_manifest.json"
        manifest_file.write_text(canonical_json(manifest), encoding="utf-8")

        return json.dumps({
            "status": "PACKAGE_CREATED",
            "package_directory": str(out_p),
            "package_digest": manifest["canonical_digest"],
            "signal_projection_sha256": proj_hash,
            "ready_for_directive_open": True,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Intake packaging failed: {exc}"})


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
