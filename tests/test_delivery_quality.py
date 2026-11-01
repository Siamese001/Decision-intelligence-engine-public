#!/usr/bin/env python3
"""Delivery-profile and architecture-artifact gate tests."""
from __future__ import annotations

import unittest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.delivery_quality import evaluate_delivery_quality


class DeliveryQualityTests(unittest.TestCase):
    def test_internal_technical_output_does_not_get_client_style_gate(self) -> None:
        result = evaluate_delivery_quality(
            run_id="run-delivery-internal",
            answer={"technical_detail": "L2D and X1 receipts"},
            delivery_profile="internal_technical", artifact_type="evidence_export",
            coverage_complete=True,
        )
        validate_artifact(result)
        self.assertEqual("PASS", result["status"])

    def test_undefined_client_jargon_blocks_release(self) -> None:
        result = evaluate_delivery_quality(
            run_id="run-delivery-client",
            answer={"recommendation": "Use M-A and P/V patterns."},
            delivery_profile="client_executive", artifact_type="decision_brief",
            coverage_complete=True,
        )
        self.assertEqual("FAIL", result["status"])
        self.assertIn("UNDEFINED_CLIENT_TERM", result["defect_codes"])

    def test_architecture_deck_requires_rendered_visual_gates(self) -> None:
        result = evaluate_delivery_quality(
            run_id="run-delivery-deck",
            answer={"recommendation": "Adopt a governed agent architecture."},
            delivery_profile="client_executive", artifact_type="architecture_deck",
            coverage_complete=True,
        )
        self.assertEqual("FAIL", result["status"])
        self.assertIn("RENDER_REVIEW_MISSING", result["defect_codes"])
        self.assertIn("PRINCIPLE_MAPPING_FAILED", result["defect_codes"])

    def test_rendered_architecture_deck_can_pass_all_gates(self) -> None:
        result = evaluate_delivery_quality(
            run_id="run-delivery-deck-pass",
            answer={"recommendation": "Adopt a governed agent architecture."},
            delivery_profile="client_executive", artifact_type="architecture_deck",
            coverage_complete=True,
            render_validation={
                "render_review": True, "visual_hierarchy": True,
                "principle_mapping": True, "persona_relevance": True,
            },
        )
        self.assertEqual("PASS", result["status"])


if __name__ == "__main__":
    unittest.main()
