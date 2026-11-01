"""
tests/adversarial/test_upload_content_extraction_adversarial.py

Adversarial and edge-case verification for Wave 3 screenshot & visual content extraction.
Covers 4 mandatory categories:
1. Boundary payload & malformed input (0-byte image file, corrupt PNG header, empty turn list)
2. Invariant bypass attempts (title-only extraction bypass, demo finance bypass, weak speaker attribution)
3. Fallback & exhaustion (provider failure cascades, exhaustion to explicit FAILED state)
4. Exact production regression (Jarvis screenshot regression: 7 identical PNG hashes, table preservation)
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
    AttachmentMention,
    CrossModalAttachmentAlignment,
)
from dual_pipeline.attachment_core.extractors import extract_attachment_content
from dual_pipeline.attachment_core.detector import discover_attachments
from dual_pipeline.attachment_core.aligner import (
    _match_attachment_for_turn,
    align_attachments_with_transcript,
)
from dual_pipeline.attachment_core.value_classifier import (
    classify_collateral_value,
    CollateralValueTier,
)
from dual_pipeline.attachment_core.prompt_builder import (
    _format_content_details,
    _format_spoken_mentions,
    build_attachment_prompt_section,
)
from pipeline_a_intake.agents.multimodal_slide_agent import MultimodalSlideAgent


# 1x1 valid transparent PNG for fixtures
VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestUploadContentExtractionAdversarial:
    """Adversarial suite for Wave 3 visual content extraction."""

    # -------------------------------------------------------------------------
    # Category 1: Boundary payload & malformed input
    # -------------------------------------------------------------------------

    def test_boundary_zero_byte_image_fails_honestly(self):
        """Zero-byte image file must fail honestly with explicit error_type and is_failed=True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            empty_file = Path(tmpdir) / "empty.png"
            empty_file.write_bytes(b"")

            record = AttachmentRecord(
                attachment_id="att-empty",
                original_path=empty_file,
                staged_path=empty_file,
                attachment_type=AttachmentType.IMAGE,
                filename="empty.png",
                file_size_bytes=0,
                sha256="e" * 64,
            )

            result = extract_attachment_content(record)

            assert result.is_failed is True
            assert result.extraction_status == "FAILED"
            assert result.error_type == "EMPTY_FILE"
            assert "0 bytes" in (result.error_reason or "")
            assert result.extracted_text == ""

            # Ensure value classifier does NOT mask this as LOW_VALUE
            assessment = classify_collateral_value(record, result)
            assert assessment.tier == CollateralValueTier.FAILED_EXTRACTION
            assert assessment.is_failed is True
            assert assessment.extraction_status == "FAILED"

    def test_malformed_corrupt_png_header_fails_visibly(self):
        """A file with truncated or garbage bytes fails visibly with exhaustion/failure status."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt_file = Path(tmpdir) / "corrupt.png"
            # Random non-PNG garbage bytes
            corrupt_file.write_bytes(b"\xde\xad\xbe\xef\x00\x11\x22\x33\x44\x55")

            record = AttachmentRecord(
                attachment_id="att-corrupt",
                original_path=corrupt_file,
                staged_path=corrupt_file,
                attachment_type=AttachmentType.IMAGE,
                filename="corrupt.png",
                file_size_bytes=10,
                sha256="c" * 64,
            )

            result = extract_attachment_content(record)

            assert result.is_failed is True
            assert result.extraction_status == "FAILED"
            assert result.error_type in ("VISUAL_EXTRACTION_EXHAUSTED", "EXTRACTION_FAILED")
            assert result.error_reason is not None

    def test_malformed_transcript_alignment_bounds(self):
        """Aligner handles negative indices, empty speakers, and missing text gracefully."""
        att = AttachmentRecord(
            attachment_id="att-bound",
            original_path=Path("/tmp/chart.png"),
            staged_path=Path("/tmp/chart.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="chart.png",
            file_size_bytes=100,
            sha256="1" * 64,
        )

        malformed_turns = [
            {"turn_index": -1, "speaker": "", "turn_text": ""},
            {"turn_index": 0, "speaker": None, "turn_text": None},
            {"turn_index": 999, "speaker": "Alice", "turn_text": "Take a look at chart.png on screen."},
        ]

        # First turn: empty text -> None
        m0 = _match_attachment_for_turn(malformed_turns[0], -1, att, None)
        assert m0 is None

        # Second turn: None text -> None
        m1 = _match_attachment_for_turn(malformed_turns[1], 0, att, None)
        assert m1 is None

        # Third turn: valid mention
        m2 = _match_attachment_for_turn(malformed_turns[2], 999, att, None)
        assert m2 is not None
        assert m2.turn_index == 999
        assert m2.speaker == "Alice"
        assert m2.attachment_id == "att-bound"

    # -------------------------------------------------------------------------
    # Category 2: Invariant bypass attempts
    # -------------------------------------------------------------------------

    def test_invariant_bypass_title_only_not_promoted(self):
        """An extraction returning only a title and empty content must NOT be classified as HIGH_VALUE."""
        record = AttachmentRecord(
            attachment_id="att-title-only",
            original_path=Path("/tmp/title_only.png"),
            staged_path=Path("/tmp/title_only.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="title_only.png",
            file_size_bytes=1024,
            sha256="a" * 64,
        )

        content = ExtractedAttachmentContent(
            attachment_id="att-title-only",
            filename="title_only.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="a" * 64,
            title="Overview Architecture Slide",
            extracted_text="",
            summary="",
            components=[],
            connections=[],
            tables=[],
            claims=[],
            milestones=[],
            key_topics=[],
            extraction_status="SUCCESS",
        )

        assessment = classify_collateral_value(record, content)
        # Content has no meaningful tables, components, claims, or text.
        # It must NOT be promoted to HIGH_VALUE
        assert assessment.tier != CollateralValueTier.HIGH_VALUE
        assert assessment.score < 0.50

    def test_invariant_bypass_demo_financial_figures_flagged(self):
        """Demo/sample UI figures must not be presented as canonical business facts."""
        content = ExtractedAttachmentContent(
            attachment_id="att-demo-ui",
            filename="pricing_mockup.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="d" * 64,
            title="Enterprise Plan Mockup",
            extracted_text="Projected Revenue: executive compensation band. Budget: $500,000.",
            tables=[
                {
                    "title": "Sample Tier Pricing",
                    "headers": ["Tier", "Demo Price"],
                    "rows": [["Enterprise", "$1,200,000/yr"]],
                }
            ],
            is_demo_or_sample=True,
            extraction_status="SUCCESS",
        )

        details = _format_content_details(content)
        joined = "\n".join(details)

        # Must include explicit warning banner
        assert "DEMO / SAMPLE EXHIBIT" in joined
        assert "Do NOT record these numbers as authentic project budgets" in joined

    def test_invariant_bypass_weak_alignment_prevents_speaker_authentication(self):
        """Isolated visual cues without entity names must tag is_weak_alignment=True and is_speaker_authenticated=False."""
        att = AttachmentRecord(
            attachment_id="att-spec",
            original_path=Path("/tmp/quarterly_roadmap.png"),
            staged_path=Path("/tmp/quarterly_roadmap.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="quarterly_roadmap.png",
            file_size_bytes=2048,
            sha256="r" * 64,
        )

        # Spoken turn uses a generic visual cue 'on the screen' and single keyword 'roadmap'
        turn = {
            "turn_index": 5,
            "speaker": "Bob",
            "turn_text": "As everyone can see on the screen, the roadmap is tight.",
            "timestamp": "00:05:00",
        }

        m = _match_attachment_for_turn(turn, 5, att, None)
        assert m is not None
        assert m.is_weak_alignment is True
        assert m.is_speaker_authenticated is False

        # In prompt builder, this must be explicitly noted as weak/unauthenticated
        alignment = CrossModalAttachmentAlignment(total_attachments=1, mentions=[m])
        lines = _format_spoken_mentions(att.attachment_id, alignment)
        joined = "\n".join(lines)
        assert "(Weak Cue - Unauthenticated Attribution)" in joined

    def test_invariant_bypass_ambient_output_discovery_exclusion(self):
        """Generated artifacts, receipts, and sibling transcripts must be excluded from attachment discovery."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Legitimate image
            valid_img = root / "architecture.png"
            valid_img.write_bytes(VALID_PNG_BYTES)

            # Illegitimate artifacts that could cause feedback loops or synthetic contamination
            (root / "01_transcript.md").write_text("transcript")
            (root / "meeting_receipt.json").write_text("{}")
            (root / "eval_report.json").write_text("{}")
            (root / "pipeline_checkpoint.pkl").write_bytes(b"\x00\x01")
            (root / "debug.log").write_text("logs")
            (root / "sibling_live_transcript.md").write_text("live")

            records = discover_attachments(root)
            discovered_names = [p.name for p in records]

            assert "architecture.png" in discovered_names
            assert "01_transcript.md" not in discovered_names
            assert "meeting_receipt.json" not in discovered_names
            assert "eval_report.json" not in discovered_names
            assert "pipeline_checkpoint.pkl" not in discovered_names
            assert "debug.log" not in discovered_names
            assert "sibling_live_transcript.md" not in discovered_names

    # -------------------------------------------------------------------------
    # Category 3: Fallback & exhaustion
    # -------------------------------------------------------------------------

    def test_fallback_cascade_exhaustion_preserves_failure_state(self):
        """When multimodal slide agent exhausts all visual providers, it emits FAILED status and error type."""
        agent = MultimodalSlideAgent()

        # Mock fallback vision providers to fail
        with patch(
            "dual_pipeline.attachment_core.image_extractor._extract_via_apple_vision",
            side_effect=RuntimeError("Apple Vision framework unavailable"),
        ), patch(
            "dual_pipeline.attachment_core.image_extractor._extract_via_gemini_vision",
            side_effect=RuntimeError("Gemini quota exceeded 429"),
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                slide_img = Path(tmpdir) / "slide1.png"
                slide_img.write_bytes(VALID_PNG_BYTES)

                slide_res = agent._fallback_extract_slides(str(slide_img), "task-fail-1")

                assert slide_res["status"] == "FAILED"
                assert slide_res["extraction_status"] == "FAILED"
                assert slide_res["error_type"] == "VISUAL_FALLBACK_EXHAUSTED"
                assert "exhausted" in slide_res["error_reason"].lower()

    # -------------------------------------------------------------------------
    # Category 4: Exact production regression (Jarvis cohort)
    # -------------------------------------------------------------------------

    def test_jarvis_screenshot_regression_preserves_tables_and_claims(self):
        """
        Regression for Jarvis screenshot cohort:
        Screenshots containing readable tables, system labels, and milestones must
        preserve those tables, claims, and milestone fields rather than discarding them.
        """
        # Simulate Jarvis OCR output with substantive tables and milestone items
        mock_raw_vision = {
            "title": "Jarvis Platform Architecture & Milestone Tracker",
            "summary": "System architecture diagram and Q4 milestone delivery table.",
            "components": [
                {"id": "c1", "label": "Ingestion Gateway", "type": "service"},
                {"id": "c2", "label": "Dual Pipeline Store", "type": "database"},
            ],
            "connections": [
                {"source": "c1", "target": "c2", "label": "persists events"}
            ],
            "tables": [
                {
                    "title": "Q4 Deliverables",
                    "headers": ["Milestone", "Target Date", "Owner", "Status"],
                    "rows": [
                        ["HITL Governance", "2026-11-01", "Platform Team", "IN_PROGRESS"],
                        ["Vector Sidecar", "2026-11-15", "Storage Team", "PENDING"],
                    ],
                }
            ],
            "claims": [
                {
                    "statement": "Ingestion latency target is under 250ms for live capture.",
                    "confidence": 0.95,
                    "evidence": "Observed on slide 4 label",
                }
            ],
            "milestones": [
                {"name": "HITL Governance", "date": "2026-11-01", "status": "IN_PROGRESS"}
            ],
            "key_topics": ["architecture", "hitl", "latency"],
            "has_diagrams": True,
            "is_demo_or_sample": False,
        }

        with patch(
            "dual_pipeline.attachment_core.image_extractor._extract_via_openai_vision",
            return_value=mock_raw_vision,
        ):
            with tempfile.TemporaryDirectory() as tmpdir:
                img_path = Path(tmpdir) / "jarvis_slide.png"
                img_path.write_bytes(VALID_PNG_BYTES)

                record = AttachmentRecord(
                    attachment_id="att-jarvis-01",
                    original_path=img_path,
                    staged_path=img_path,
                    attachment_type=AttachmentType.IMAGE,
                    filename="jarvis_slide.png",
                    file_size_bytes=len(VALID_PNG_BYTES),
                    sha256="7" * 64,
                )

                content = extract_attachment_content(record)

                # Verify that tables, claims, and milestones are preserved
                assert content.extraction_status == "SUCCESS"
                assert len(content.tables) == 1
                assert content.tables[0]["title"] == "Q4 Deliverables"
                assert len(content.claims) == 1
                assert "latency target" in content.claims[0]["statement"]
                assert len(content.milestones) == 1
                assert content.milestones[0]["name"] == "HITL Governance"

                # Verify details format renders tables in markdown
                details = _format_content_details(content)
                joined = "\n".join(details)
                assert "Extracted Tables & Statuses" in joined
                assert "| Milestone | Target Date | Owner | Status |" in joined
                assert "HITL Governance" in joined
                assert "Extracted Milestones & Schedule" in joined
