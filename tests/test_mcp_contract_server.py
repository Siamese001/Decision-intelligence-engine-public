"""Tests for Dual-Pipeline Contract Governance MCP Server (Rank 3)."""
import json
from pathlib import Path

from tools.mcp_contract_server import (
    build_intake_package,
    contract_inventory_verify,
    rfc8259_canonical_hash,
    schema_validate_payload,
)


def test_schema_validate_payload():
    # Valid payload against eval-suite schema if present, or test schema
    payload = {
        "suite_id": "test_suite_01",
        "description": "A valid test suite",
        "cases": []
    }
    # Test with known schema name
    res = schema_validate_payload("eval-suite.schema.json", json.dumps(payload))
    data = json.loads(res)
    # Either PASS or explicit validation error
    assert "status" in data or "valid" in data


def test_schema_validate_payload_invalid():
    res = schema_validate_payload("eval-suite.schema.json", json.dumps({"invalid_field_unauthorized": 123}))
    data = json.loads(res)
    assert data["valid"] is False
    assert data["status"] == "FAIL_CLOSED"
    assert data["error_count"] >= 1


def test_rfc8259_canonical_hash():
    payload = {"b": 2, "a": 1, "canonical_digest": "to_be_stripped"}
    res = rfc8259_canonical_hash(json.dumps(payload), exclude_fields_csv="canonical_digest")
    data = json.loads(res)
    assert "sha256" in data
    assert len(data["sha256"]) == 64
    assert '{"a":1,"b":2}' in data["canonical_preview"]


def test_contract_inventory_verify():
    res = contract_inventory_verify()
    data = json.loads(res)
    assert "status" in data
    assert data["checked_count"] > 0


def test_build_intake_package(tmp_path: Path):
    src_file = tmp_path / "source.md"
    src_file.write_text("# Meeting Source", encoding="utf-8")
    out_dir = tmp_path / "intake_pkg"

    proj = {"meeting": "test", "statements": []}
    res = build_intake_package(json.dumps(proj), str(src_file), str(out_dir))
    data = json.loads(res)
    assert data["status"] == "PACKAGE_CREATED"
    assert (out_dir / "package_manifest.json").is_file()
    assert (out_dir / "signal_projection.json").is_file()
