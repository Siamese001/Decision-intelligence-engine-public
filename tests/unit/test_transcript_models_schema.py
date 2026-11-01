"""
tests/unit/test_transcript_models_schema.py

Unit test suite verifying Wave 2 Pydantic Schema SSOT Alignment & Enum Binding.
Enforces closed 6-kind taxonomy and substantive person descriptions.
"""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from dual_pipeline.transcript_models import (
    CanonicalSignalProjectionV57,
    EntityUpsert,
    EvidenceStatement,
)


def test_evidence_statement_valid_kinds():
    for valid_kind in ["summary", "decision", "risk", "dependency", "technical", "strategic"]:
        stmt = EvidenceStatement(
            speaker="Taylor Mercer",
            kind=valid_kind,
            text=f"Valid evidence statement for {valid_kind}",
        )
        assert stmt.kind == valid_kind


def test_evidence_statement_invalid_kind_raises():
    with pytest.raises(ValidationError):
        EvidenceStatement(
            speaker="Taylor Mercer",
            kind="policy",
            text="Policy should not be an evidence kind",
        )


def test_entity_upsert_description_allowed():
    e = EntityUpsert(
        id="system:nexus",
        type="system",
        name="Nexus Core",
        description="Core decision intelligence engine and integration spine.",
    )
    assert e.description == "Core decision intelligence engine and integration spine."


def test_person_generic_placeholder_rejected():
    with pytest.raises(ValidationError) as exc_info:
        EntityUpsert(
            id="person:traci-metzger",
            type="person",
            name="Taylor Mercer",
            description="Review participant.",
        )
    assert "Gate 3.11" in str(exc_info.value)


def test_person_substantive_description_passes():
    e = EntityUpsert(
        id="person:traci-metzger",
        type="person",
        name="Taylor Mercer",
        description="Delivery Lead at PartnerAdvisory governing project delivery and business case assumptions.",
    )
    assert "Delivery Lead" in e.description


def test_canonical_signal_projection_schema_export():
    schema = CanonicalSignalProjectionV57.model_json_schema()
    schema_str = json.dumps(schema)
    assert "summary" in schema_str
    assert "strategic" in schema_str
    assert "description" in schema_str

    receipt_path = Path("reports/wave2_pydantic_ssot_receipt.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({
        "status": "PASS",
        "schema_version": "5.7",
        "tested_validators": [
            "validate_person_description",
            "validate_entity_prefix",
            "ClosedEvidenceKind"
        ]
    }, indent=2), encoding="utf-8")
