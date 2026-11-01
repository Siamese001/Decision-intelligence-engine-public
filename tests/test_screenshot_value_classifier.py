"""tests/test_screenshot_value_classifier.py

Wave 2: Unit test verifying screenshot value classification and prompt section injection.
"""
from __future__ import annotations

import unittest
from pathlib import Path

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


class TestScreenshotValueClassifier(unittest.TestCase):
    """Verifies that screenshots are evaluated for substantive value-add."""

    def setUp(self) -> None:
        self.record = AttachmentRecord(
            attachment_id="att-test1234",
            filename="system_architecture_diagram.png",
            original_path=Path("/tmp/system_architecture_diagram.png"),
            staged_path=Path("/tmp/referenced_artifacts/system_architecture_diagram.png"),
            attachment_type=AttachmentType.IMAGE,
            sha256="abcdef1234567890",
            mime_type="image/png",
            file_size_bytes=10240,
            relative_staged_path="referenced_artifacts/system_architecture_diagram.png",
            attribution_mode="COLLATERAL_REVIEWED",
        )

    def test_classify_high_value_architecture(self) -> None:
        """Asserts diagrams with components, connections, and metrics classify as HIGH_VALUE."""
        content = ExtractedAttachmentContent(
            attachment_id="att-test1234",
            filename="system_architecture_diagram.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="abcdef1234567890",
            extracted_text="Project LENS Architecture Pipeline: VLM Nameplate Extraction engine achieving 90% throughput across AWS S3 and GCP.",
            summary="System Architecture for LENS Ingestion Pipeline",
            components=[
                {"component_ref": "comp-lens-vlm", "label": "VLM Engine"},
                {"component_ref": "comp-s3-ingress", "label": "S3 Ingress"},
                {"component_ref": "comp-rule-builder", "label": "Rule Builder"},
            ],
            connections=[
                {"from_ref": "comp-s3-ingress", "to_ref": "comp-lens-vlm", "predicate": "FEEDS_INTO"},
            ],
            has_diagrams=True,
        )
        assessment = classify_collateral_value(self.record, content)
        self.assertEqual(assessment.tier, CollateralValueTier.HIGH_VALUE)
        self.assertTrue(assessment.is_value_added)
        self.assertGreaterEqual(assessment.score, 0.5)

    def test_classify_low_value_decorative(self) -> None:
        """Asserts decorative logos or title slides classify as LOW_VALUE."""
        record_low = AttachmentRecord(
            attachment_id="att-logo",
            filename="partner_advisory_company_logo.png",
            original_path=Path("/tmp/partner_advisory_company_logo.png"),
            staged_path=Path("/tmp/referenced_artifacts/partner_advisory_company_logo.png"),
            attachment_type=AttachmentType.IMAGE,
            sha256="1122334455667788",
            mime_type="image/png",
            file_size_bytes=2048,
            relative_staged_path="referenced_artifacts/partner_advisory_company_logo.png",
        )
        content_low = ExtractedAttachmentContent(
            attachment_id="att-logo",
            filename="partner_advisory_company_logo.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="1122334455667788",
            extracted_text="PartnerAdvisory Consulting",
            summary="Company Logo",
            components=[],
            connections=[],
            has_diagrams=False,
        )
        assessment = classify_collateral_value(record_low, content_low)
        self.assertEqual(assessment.tier, CollateralValueTier.LOW_VALUE)
        self.assertFalse(assessment.is_value_added)
        self.assertLess(assessment.score, 0.3)

    def test_prompt_builder_injects_value_directives(self) -> None:
        """Asserts prompt builder injects MANDATORY VALUE REFLECTION for high-value items."""
        content = ExtractedAttachmentContent(
            attachment_id="att-test1234",
            filename="system_architecture_diagram.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="abcdef1234567890",
            extracted_text="Architecture: Ingestion Pipeline connecting AWS S3 to LENS VLM with 90% accuracy.",
            summary="Architecture Flow",
            components=[{"component_ref": "comp-vlm", "label": "VLM"}],
            connections=[{"from_ref": "comp-s3", "to_ref": "comp-vlm", "predicate": "CALLS"}],
            has_diagrams=True,
        )
        prompt_block = build_attachment_prompt_section(
            attachments=[self.record],
            extracted_contents={"att-test1234": content},
        )
        self.assertIn("MANDATORY VALUE REFLECTION", prompt_block)
        self.assertIn("HIGH_VALUE", prompt_block)


if __name__ == "__main__":
    unittest.main()
