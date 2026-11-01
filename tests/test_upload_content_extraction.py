"""
tests/test_upload_content_extraction.py

Unit tests for Wave 3: Extract screenshot content and preserve failures honestly.
Verifies table/milestone preservation, honest failure states (no silent LOW_VALUE masking),
demo/sample identification, prompt table formatting, and weak alignment tagging.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.attachment_core.aligner import (
    align_attachments_with_transcript,
    _match_attachment_for_turn,
)
from dual_pipeline.attachment_core.detector import (
    _is_valid_attachment_file,
    discover_attachments,
)
from dual_pipeline.attachment_core.extractors import extract_attachment_content
from dual_pipeline.attachment_core.prompt_builder import (
    build_attachment_prompt_section,
    _format_content_details,
    _format_single_attachment_block,
)
from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)
from dual_pipeline.attachment_core.value_classifier import (
    CollateralValueAssessment,
    CollateralValueTier,
    classify_collateral_value,
)
from pipeline_a_intake.agents.multimodal_slide_agent import _format_vision_slide


class TestVisualContentExtraction:
    """Unit tests for multimodal content extraction contracts and failure preservation."""

    def test_multimodal_slide_extractor_preserves_tables_and_milestones_without_topics(self):
        """Preserves claims, tables, and milestones even when 'topics' list is empty."""
        raw_res = {
            "title": "Release Roadmap",
            "topics": [],  # Empty topics
            "components": [],
            "claims": [{"claim": "Target completion by Q3"}],
            "tables": [{"headers": ["Milestone", "Date"], "rows": [["UAT Phase", "2026-07-01"]]}],
            "milestones": [{"name": "UAT Phase", "date": "2026-07-01", "status": "PENDING"}],
            "text": "Release Roadmap\nTarget completion by Q3\nUAT Phase: 2026-07-01",
        }
        slide = _format_vision_slide(raw_res, fallback_title="Fallback Title")
        assert slide is not None
        assert slide["title"] == "Release Roadmap"
        assert len(slide["tables"]) == 1
        assert len(slide["milestones"]) == 1
        assert slide["milestones"][0]["name"] == "UAT Phase"
        assert len(slide["claims"]) == 1

    def test_failed_extraction_becomes_failed_extraction_tier_not_low_value(self):
        """A failed extraction must resolve to FAILED_EXTRACTION tier, never masquerading as LOW_VALUE."""
        content = ExtractedAttachmentContent(
            attachment_id="att-failed-1",
            filename="unreadable_diagram.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="0" * 64,
            title="Unreadable Diagram",
            extracted_text="",
            extraction_status="FAILED",
            error_reason="VISUAL_EXTRACTION_EXHAUSTED",
            is_failed=True,
        )
        record = AttachmentRecord(
            attachment_id="att-failed-1",
            original_path=Path("/tmp/unreadable_diagram.png"),
            staged_path=Path("/tmp/staged/unreadable_diagram.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="unreadable_diagram.png",
            file_size_bytes=1024,
            sha256="0" * 64,
        )
        assessment = classify_collateral_value(record, content)
        assert assessment.tier == CollateralValueTier.FAILED_EXTRACTION
        assert assessment.tier != CollateralValueTier.LOW_VALUE
        assert assessment.is_failed is True
        assert assessment.is_value_added is False
        assert "VISUAL_EXTRACTION_EXHAUSTED" in assessment.reasons[0]

    def test_genuine_blank_image_excluded_with_reason(self):
        """Genuine blank/template image receives LOW_VALUE with explicit substantive exclusion reason."""
        content = ExtractedAttachmentContent(
            attachment_id="att-blank-1",
            filename="blank_template.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="1" * 64,
            title="Blank Template",
            extracted_text="",
            extraction_status="EMPTY",
            is_failed=False,
        )
        record = AttachmentRecord(
            attachment_id="att-blank-1",
            original_path=Path("/tmp/blank_template.png"),
            staged_path=Path("/tmp/staged/blank_template.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="blank_template.png",
            file_size_bytes=512,
            sha256="1" * 64,
        )
        assessment = classify_collateral_value(record, content)
        assert assessment.tier == CollateralValueTier.LOW_VALUE
        assert assessment.is_failed is False
        assert assessment.is_value_added is False
        assert any("Below substantive threshold" in r for r in assessment.reasons)

    def test_demo_sample_finance_flagged(self):
        """Demonstration and sample budget screenshots are flagged as demo data."""
        content = ExtractedAttachmentContent(
            attachment_id="att-demo-1",
            filename="budget_demo.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="2" * 64,
            title="Demo Budget Table",
            extracted_text="Budget: $100,000 (Sample UI)",
            tables=[{"headers": ["Item", "Cost"], "rows": [["Demo Service", "$10,000"]]}],
            is_demo_or_sample=True,
        )
        record = AttachmentRecord(
            attachment_id="att-demo-1",
            original_path=Path("/tmp/budget_demo.png"),
            staged_path=Path("/tmp/staged/budget_demo.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="budget_demo.png",
            file_size_bytes=2048,
            sha256="2" * 64,
        )
        assessment = classify_collateral_value(record, content)
        assert any("demo" in r.lower() or "mockup" in r.lower() for r in assessment.reasons)

        details = _format_content_details(content)
        assert any("DEMO / SAMPLE EXHIBIT" in line for line in details)

    def test_prompt_builder_renders_tables_and_milestones(self):
        """Prompt builder renders extracted tables and milestones into markdown blocks."""
        content = ExtractedAttachmentContent(
            attachment_id="att-table-1",
            filename="milestones.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="3" * 64,
            title="Milestone Schedule",
            extracted_text="Sprint Milestones",
            tables=[{"headers": ["Sprint", "Date"], "rows": [["Sprint 1", "2026-06-16"]]}],
            milestones=[{"name": "Sprint 1", "date": "2026-06-16", "status": "COMPLETED"}],
        )
        details = _format_content_details(content)
        joined = "\n".join(details)
        assert "Extracted Milestones & Schedule" in joined
        assert "Sprint 1" in joined
        assert "2026-06-16" in joined
        assert "Extracted Tables & Statuses" in joined
        assert "| Sprint | Date |" in joined

    def test_prompt_builder_warns_on_failed_extraction(self):
        """Prompt builder emits FAILED EXTRACTION / QUARANTINE block when extraction failed."""
        content = ExtractedAttachmentContent(
            attachment_id="att-fail-prompt",
            filename="broken.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="4" * 64,
            extraction_status="FAILED",
            error_reason="OCR engine crash",
            is_failed=True,
        )
        record = AttachmentRecord(
            attachment_id="att-fail-prompt",
            original_path=Path("/tmp/broken.png"),
            staged_path=Path("/tmp/staged/broken.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="broken.png",
            file_size_bytes=100,
            sha256="4" * 64,
        )
        lines = _format_single_attachment_block(record, 1, content, None)
        joined = "\n".join(lines)
        assert "FAILED EXTRACTION / QUARANTINED" in joined
        assert "OCR engine crash" in joined

    def test_weak_alignment_does_not_authenticate_speaker(self):
        """Generic visual cues like 'on the screen' paired with isolated keywords create weak alignments without authenticated speaker."""
        record = AttachmentRecord(
            attachment_id="att-weak",
            original_path=Path("/tmp/system_deployment_diagram.png"),
            staged_path=Path("/tmp/staged/system_deployment_diagram.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="system_deployment_diagram.png",
            file_size_bytes=1024,
            sha256="5" * 64,
        )
        turn = {
            "speaker": "Speaker 1",
            "turn_text": "Looking at the screen, the deployment is in progress.",
            "timestamp": "00:05:00",
        }
        mention = _match_attachment_for_turn(turn, 0, record, None)
        assert mention is not None
        assert mention.is_weak_alignment is True
        assert mention.is_speaker_authenticated is False

    def test_detector_excludes_receipts_and_sibling_transcripts(self):
        """Detector rejects receipts, summaries, checkpoints, and sibling transcripts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            t_file = root / "meeting_transcript.md"
            t_file.touch()

            # Sibling transcript
            sib_transcript = root / "prior_meeting_transcript.txt"
            sib_transcript.touch()
            assert not _is_valid_attachment_file(sib_transcript, t_file)

            # Execution receipt
            receipt = root / "pipeline_execution_receipt.json"
            receipt.touch()
            assert not _is_valid_attachment_file(receipt, t_file)

            # Checkpoint
            ckpt = root / "output_3.ckpt.json"
            ckpt.touch()
            assert not _is_valid_attachment_file(ckpt, t_file)

            # Valid image attachment
            img = root / "system_architecture.png"
            img.touch()
            assert _is_valid_attachment_file(img, t_file)

    def test_detector_binds_exact_explicit_paths(self):
        """Explicit attachment_paths bind strictly and ignore ambient directory files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            slide_a = root / "slide_a.png"
            slide_a.touch()
            ambient_slide = root / "ambient.png"
            ambient_slide.touch()

            # Caller explicitly provides only slide_a
            discovered = discover_attachments(attachment_paths=[slide_a])
            assert len(discovered) == 1
            assert discovered[0].resolve() == slide_a.resolve()
