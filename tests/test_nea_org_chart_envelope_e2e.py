"""E2E and cryptographic sealing test for NEA/NEE visual document intake envelopes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import pytest
import jsonschema

from dual_pipeline.core import artifact_digest
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.visual_envelope import build_visual_intake_envelope

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "nea_org_chart_screenshot_fixture.png"
SCHEMA_PATH = ROOT / "contracts" / "dual_pipeline" / "schemas" / "evidence-envelope-intake.schema.json"
COMMON_SCHEMA_PATH = ROOT / "contracts" / "dual_pipeline" / "schemas" / "common.schema.json"


def _get_schema_validator():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    common_schema = json.loads(COMMON_SCHEMA_PATH.read_text(encoding="utf-8"))
    registry = jsonschema.validators.validator_for(schema)
    schema_store = {
        schema["$id"]: schema,
        common_schema["$id"]: common_schema,
    }
    resolver = jsonschema.RefResolver.from_schema(schema, store=schema_store)
    return registry(schema, resolver=resolver)


def test_visual_envelope_schema_and_rfc8259_sealing():
    """Verify that build_visual_intake_envelope conforms to schema and seals canonically."""
    assert FIXTURE_PATH.exists(), f"Fixture {FIXTURE_PATH} must exist"
    envelope = build_visual_intake_envelope(FIXTURE_PATH, title="Scott Herold Org Chart")

    validator = _get_schema_validator()
    validator.validate(envelope)

    # Cryptographic integrity check
    claimed_digest = envelope["canonical_digest"]["value"]
    computed_digest = artifact_digest(envelope)
    assert claimed_digest == computed_digest, "Canonical RFC 8259 digest must match computed digest"

    # Source hash check
    expected_file_hash = hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert envelope["meeting"]["source_hash"] == expected_file_hash

    # Metadata check
    assert envelope["adapter"]["name"] == "nee-visual-document-intake"
    assert envelope["extensions"]["source.profile"]["media_modality"] == "visual"
    assert envelope["extensions"]["visual.metadata"]["ocr_line_count"] >= 5


def test_document_pipeline_content_loader_image_dispatch():
    """Verify load_document_content extracts authentic OCR text when visual intake is enabled."""
    assert FIXTURE_PATH.exists()
    metadata: dict = {"enable_visual_ocr": True}
    text, sha, fmt = load_document_content(FIXTURE_PATH, metadata=metadata)

    assert fmt in {"png", "jpg"}
    assert sha == hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
    assert "Herold, Scott" in text or "Scott" in text
    assert "Sankaraiah" in text or "Argentieri" in text

    # Verify coverage reporting
    assert metadata["_source_coverage"]["complete"] is True
    assert metadata["_source_coverage"]["ocr_text_length"] > 0
    assert len(metadata["visual_extraction"]["components"]) > 0


def test_document_pipeline_rejects_unflagged_raw_image():
    """Verify negative invariant: unflagged raw image files fail closed without visual adapter flag."""
    assert FIXTURE_PATH.exists()
    with pytest.raises(ValueError, match="Binary image format"):
        load_document_content(FIXTURE_PATH)


def test_apm_workflow_roster_extraction_and_sealing():
    """Verify high-fidelity OCR and envelope generation for FinAnalytics APM Workflow screenshot."""
    apm_path = (
        ROOT
        / "output"
        / "project_beacon"
        / "10-06-2026"
        / "project_beacon_Sprint_Speaker_Alpha_PartnerAdvisory"
        / "referenced_artifacts"
        / "Speaker_Beta_FinAnalytics_Carlos_APM_Workflow.png"
    )
    if not apm_path.exists():
        pytest.skip(f"Live artifact {apm_path} not available in worktree")

    envelope = build_visual_intake_envelope(apm_path, title="FinAnalytics APM Workflow Roster")
    validator = _get_schema_validator()
    validator.validate(envelope)

    # Verify key APM leaders are present in OCR statements
    all_stmt_text = " ".join(
        s["text"] for spk in envelope["speakers"] for s in spk.get("statements", [])
    )
    assert "Carlos E Sanchez" in all_stmt_text
    assert "Ben Miron" in all_stmt_text
    assert "Grace Kurian" in all_stmt_text
    assert "logan Miller" in all_stmt_text
    assert "Peter Sander" in all_stmt_text
    assert "Matthew Barrows" in all_stmt_text
