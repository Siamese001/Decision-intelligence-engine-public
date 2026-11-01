"""
tests/end_to_end/test_upload_content_extraction_e2e.py

End-to-end integration tests for Wave 3: Visual content extraction and honest failure preservation.
Tests full pipeline flow from discovery and extraction through value classification,
alignment, schema validation against contracts/dual_pipeline/schemas/visual-content.schema.json,
and prompt generation in an isolated disposable sandbox.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import jsonschema
import pytest

from dual_pipeline.attachment_core.aligner import align_attachments_with_transcript
from dual_pipeline.attachment_core.detector import discover_attachments
from dual_pipeline.attachment_core.extractors import extract_attachment_content
from dual_pipeline.attachment_core.prompt_builder import build_attachment_prompt_section
from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)
from dual_pipeline.attachment_core.value_classifier import (
    CollateralValueTier,
    classify_collateral_value,
)


class TestUploadContentExtractionE2E:
    """E2E verification of visual attachment extraction, validation, and prompt integration."""

    @pytest.fixture
    def visual_contract_schema(self) -> Dict[str, Any]:
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "contracts"
            / "dual_pipeline"
            / "schemas"
            / "visual-content.schema.json"
        )
        assert schema_path.is_file(), f"Schema not found at {schema_path}"
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def test_e2e_successful_extraction_and_schema_conformance(self, visual_contract_schema):
        """E2E flow: milestone screenshot produces structured extraction that validates against visual-content schema."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            staged_dir = tmp_root / "referenced_artifacts"
            staged_dir.mkdir(parents=True)

            img_path = staged_dir / "project_milestones_roadmap.png"
            img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)  # minimal mock PNG

            record = AttachmentRecord(
                attachment_id="att-e2e-milestones",
                original_path=img_path,
                staged_path=img_path,
                attachment_type=AttachmentType.IMAGE,
                filename="project_milestones_roadmap.png",
                file_size_bytes=img_path.stat().st_size,
                sha256="a" * 64,
                relative_staged_path="referenced_artifacts/project_milestones_roadmap.png",
            )

            # Extract content in mock/deterministic mode
            content = extract_attachment_content(record, mock_mode=True)
            assert content.extraction_status == "SUCCESS"
            assert not content.is_failed
            assert len(content.milestones) >= 1
            assert content.milestones[0]["name"] == "Sprint 1 Delivery"

            # Validate against RFC Draft 2020-12 visual content schema
            payload = content.to_dict()
            payload["extracted_at"] = "2026-10-08T12:00:00Z"
            jsonschema.validate(instance=payload, schema=visual_contract_schema)

            # Evaluate value tier
            assessment = classify_collateral_value(record, content)
            assert assessment.tier in (CollateralValueTier.HIGH_VALUE, CollateralValueTier.MEDIUM_VALUE)
            assert assessment.is_value_added is True

            # Prompt generation includes milestone block
            transcript_text = "Speaker 1: Looking at the screen, our sprint milestone is committed."
            alignment = align_attachments_with_transcript(
                transcript_text=transcript_text,
                attachments=[record],
                extracted_contents={record.attachment_id: content},
            )
            prompt_section = build_attachment_prompt_section(
                attachments=[record],
                extracted_contents={record.attachment_id: content},
                alignment=alignment,
            )
            assert "Extracted Milestones & Schedule" in prompt_section
            assert "Sprint 1 Delivery" in prompt_section

    def test_e2e_failed_extraction_quarantined_in_prompt(self, visual_contract_schema):
        """E2E flow: unreadable artifact produces honest failure status, validates against schema, and alerts prompt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            corrupt_file = tmp_root / "corrupt_data.png"
            corrupt_file.write_bytes(b"NOT_A_VALID_IMAGE_BYTES")

            record = AttachmentRecord(
                attachment_id="att-e2e-corrupt",
                original_path=corrupt_file,
                staged_path=corrupt_file,
                attachment_type=AttachmentType.IMAGE,
                filename="corrupt_data.png",
                file_size_bytes=corrupt_file.stat().st_size,
                sha256="b" * 64,
                relative_staged_path="referenced_artifacts/corrupt_data.png",
            )

            # In non-mock mode, unreadable file fails all vision engines
            content = extract_attachment_content(record, mock_mode=False)
            assert content.extraction_status == "FAILED"
            assert content.is_failed is True
            assert "VISUAL_EXTRACTION_EXHAUSTED" in (content.error_type or "")

            # Schema validation for failed extraction
            payload = content.to_dict()
            payload["extracted_at"] = "2026-10-08T12:00:00Z"
            jsonschema.validate(instance=payload, schema=visual_contract_schema)

            # Value assessment marks as FAILED_EXTRACTION
            assessment = classify_collateral_value(record, content)
            assert assessment.tier == CollateralValueTier.FAILED_EXTRACTION
            assert assessment.is_failed is True

            # Prompt section contains QUARANTINED warning
            prompt_section = build_attachment_prompt_section(
                attachments=[record],
                extracted_contents={record.attachment_id: content},
            )
            assert "FAILED EXTRACTION / QUARANTINED" in prompt_section
            assert "Do NOT hallucinate" in prompt_section

    def test_e2e_demo_finance_safeguard(self):
        """E2E flow: demo finance slide is flagged and guarded against canonical business fact promotion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            demo_img = tmp_root / "finance_demo_budget.png"
            demo_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

            record = AttachmentRecord(
                attachment_id="att-demo-fin",
                original_path=demo_img,
                staged_path=demo_img,
                attachment_type=AttachmentType.IMAGE,
                filename="finance_demo_budget.png",
                file_size_bytes=demo_img.stat().st_size,
                sha256="c" * 64,
            )

            content = extract_attachment_content(record, mock_mode=True)
            assert content.is_demo_or_sample is True
            assert len(content.tables) >= 1

            prompt_section = build_attachment_prompt_section(
                attachments=[record],
                extracted_contents={record.attachment_id: content},
            )
            assert "DEMO / SAMPLE EXHIBIT" in prompt_section
            assert "Do NOT record these numbers as authentic project budgets" in prompt_section
