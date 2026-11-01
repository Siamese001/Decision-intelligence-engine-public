"""tests/test_copilot_multimodal_framing_e2e.py

Wave 6: End-to-End Integration Test for Copilot Multimodal Framing,
Visual Collateral Value Ingestion, Operator Question Firewall (C-29),
and Response Length Budgeting.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.attachment_core.prompt_builder import (
    build_attachment_prompt_section,
)
from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)
from dual_pipeline.attachment_core.value_classifier import (
    CollateralValueTier,
    classify_collateral_value,
)
from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    validate_copilot_feedback,
)
from dual_pipeline.transcript_qa_core.gate2 import run_qa_gate_2
from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class TestCopilotMultimodalFramingE2E(unittest.TestCase):
    """End-to-End integration test covering the complete multimodal framing lifecycle."""

    def setUp(self) -> None:
        self.meeting_meta = {
            "meeting_name": "Phase1_Architecture_Sync",
            "date": "09/25/2026",
            "time": "10:00 AM - 10:45 AM ET",
            "classification": "confidential",
            "archetype": "general",
            "duration_minutes": 45,
            "attendees": [
                {"name": "Jake Stark", "role": "LENS Lead", "org": "PartnerAdvisory"},
                {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
                {"name": "Miles Erickson", "role": "Senior Engineer", "org": "PartnerAdvisory"},
            ],
            "operator_framing_questions": [
                {
                    "query": "What if we bypass the Zone 4 validation gate to accelerate throughput?",
                    "ratified": False,
                }
            ],
        }

    def test_full_copilot_multimodal_framing_lifecycle_e2e(self) -> None:
        """Exercises the complete lifecycle from collateral drop to downstream outputs."""

        # ---------------------------------------------------------
        # Phase 1: Collateral Ingestion & Substantive Value Classification
        # ---------------------------------------------------------
        high_val_att = AttachmentRecord(
            attachment_id="att-001",
            original_path=Path("/tmp/arch_zone5_diagram.png"),
            staged_path=Path("/tmp/referenced_artifacts/arch_zone5_diagram.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="arch_zone5_diagram.png",
            file_size_bytes=1048576,
            sha256="abc123sha256highvalue",
            relative_staged_path="referenced_artifacts/arch_zone5_diagram.png",
            attribution_mode="COLLATERAL_REVIEWED",
        )
        high_val_content = ExtractedAttachmentContent(
            attachment_id="att-001",
            filename="arch_zone5_diagram.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="abc123sha256highvalue",
            summary="Zone 5 straight-through processing pipeline architecture diagram",
            extracted_text="Zone 5 Pipeline: Model A and Model B agreement threshold. Target latency: <250ms.",
            components=[
                {"component_ref": "c1", "label": "Zone 5 STP Pipeline"},
                {"component_ref": "c2", "label": "Dual LLM Judge"},
                {"component_ref": "c3", "label": "Vision OCR Engine"},
                {"component_ref": "c4", "label": "Enterprise Persistence Store"},
            ],
            connections=[
                {"from_ref": "c1", "to_ref": "c2", "predicate": "routes_to"},
                {"from_ref": "c2", "to_ref": "c3", "predicate": "validates_with"},
                {"from_ref": "c3", "to_ref": "c4", "predicate": "persists_to"},
            ],
            has_diagrams=True,
        )
        assessment_high = classify_collateral_value(high_val_att, high_val_content)
        self.assertEqual(assessment_high.tier, CollateralValueTier.HIGH_VALUE)
        self.assertGreaterEqual(assessment_high.score, 0.6)

        low_val_att = AttachmentRecord(
            attachment_id="att-002",
            original_path=Path("/tmp/meeting_title_slide.png"),
            staged_path=Path("/tmp/referenced_artifacts/meeting_title_slide.png"),
            attachment_type=AttachmentType.IMAGE,
            filename="meeting_title_slide.png",
            file_size_bytes=20480,
            sha256="def456sha256lowvalue",
            relative_staged_path="referenced_artifacts/meeting_title_slide.png",
            attribution_mode="COLLATERAL_REVIEWED",
        )
        low_val_content = ExtractedAttachmentContent(
            attachment_id="att-002",
            filename="meeting_title_slide.png",
            attachment_type=AttachmentType.IMAGE,
            sha256="def456sha256lowvalue",
            summary="Meeting title slide with date",
            extracted_text="Phase 1 Architecture Sync - September 25 2026",
            components=[],
            connections=[],
        )
        assessment_low = classify_collateral_value(low_val_att, low_val_content)
        self.assertEqual(assessment_low.tier, CollateralValueTier.LOW_VALUE)

        # Build synthesis prompt section and verify value directives
        prompt_block = build_attachment_prompt_section(
            attachments=[high_val_att, low_val_att],
            extracted_contents={"att-001": high_val_content, "att-002": low_val_content},
        )
        self.assertIn("MANDATORY VALUE REFLECTION", prompt_block)
        self.assertIn("BACKGROUND REFERENCE", prompt_block)

        # ---------------------------------------------------------
        # Phase 2: Live Copilot Advisory Turn & Length Budget Validation
        # ---------------------------------------------------------
        advisor = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Phase1_Architecture_Sync",
            attendees=self.meeting_meta["attendees"],
            mock_mode=True,
        )
        advisor.record_turn("Jake Stark", "We walked through Zone 5 architecture and decided to relax STP confidence.", "05:10", 310.0)
        advisor.record_turn("Amit Ayer", "What if we bypass the Zone 4 validation gate to accelerate throughput?", "05:30", 330.0)

        feedback = advisor.evaluate_live_turns(current_time=335.0)
        self.assertIsNotNone(feedback)
        validated = validate_copilot_feedback(feedback)
        self.assertIsNotNone(validated)
        self.assertIn("speaker_summary_quotes", validated)
        self.assertIn("supporting_details_bullets", validated)

        # Enforce exact word budget bounds
        quote_words = len(validated["speaker_summary_quotes"].split())
        bullet_words = sum(len(b.split()) for b in validated["supporting_details_bullets"])
        self.assertLessEqual(quote_words, 50)
        self.assertLessEqual(bullet_words, 200)
        self.assertLessEqual(quote_words + bullet_words, 250)

        # ---------------------------------------------------------
        # Phase 3: Stage U0 Ingestion & Copilot Telemetry Quarantine
        # ---------------------------------------------------------
        u0 = U0IngestionBoundary()
        copilot_payload = json.dumps({
            "type": "COPILOT_FEEDBACK",
            "speaker_summary_quotes": validated["speaker_summary_quotes"],
            "supporting_details_bullets": validated["supporting_details_bullets"],
        })
        with self.assertRaises(ValueError) as ctx:
            u0.execute(
                copilot_payload,
                {"meeting_name": "Test", "input_path": "/tmp/01_copilot_feedback.jsonl"},
            )
        self.assertIn("C-29", str(ctx.exception))

        # ---------------------------------------------------------
        # Phase 4: Output 02 & Gate 2 Verification (C-29 Firewall & Value Summary)
        # ---------------------------------------------------------
        f2_text = """# Meeting Objective
Align on Zone 5 straight-through processing thresholds and collateral architecture.

# Key Discussion Points & Findings
- **Zone 5 Pipeline Topology**: Jake Stark reviewed the Zone 5 architecture illustrated in `arch_zone5_diagram.png`, detailing how Model A and Model B cross-validate before enterprise persistence. [05:10; 05:25]
- **Dual-Model Agreement**: The team confirmed that relaxed agreement thresholds improve processing throughput without risking accuracy. [05:40]

# Recommendations & Decisions Required
- **STP Threshold Relaxation**: Ratified relaxing Zone 5 STP confidence threshold from 95% to 75% across production workloads.

# Action & Governance Ledger
| Item | Owner | Target Date | Status |
| --- | --- | --- | --- |
| Benchmark Zone 5 STP pipeline at 75% threshold | Jake Stark | 10/15/2026 | In Progress |

# Business Meaning and Value Case
The relaxation of Zone 5 confidence directly unlocks higher throughput for client operations while preserving governance safeguards.

# Immediate Delivery Scope & Release Boundaries
Bounded to Zone 5 pipeline tuning; excludes Zone 1-4 intake modifications.

# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown
# Jake Stark — PartnerAdvisory, LENS Lead
- **Zone 5 Pipeline Design**: Jake emphasized the need for empirical validation across turbine samples before finalizing production settings.
"""
        meta2 = dict(self.meeting_meta)
        gate2_res = run_qa_gate_2(f2_text, metadata=meta2)
        self.assertTrue(
            gate2_res["gates"]["Gate_2_13_Operator_Thought_Framing_Firewall"]["passed"],
            f"Gate 2.13 failed: {gate2_res['failures']}",
        )

        # ---------------------------------------------------------
        # Phase 5: Output 03 & Gate 3 Verification (C-29 Exclusion & High-Value Entity)
        # ---------------------------------------------------------
        f3_payload: Dict[str, Any] = {
            "schema_version": "5.7",
            "metadata": {
                "meeting_name": "Phase1_Architecture_Sync",
                "date": "09/25/2026",
            },
            "evidence_index": {
                "ev-001": {
                    "verbatim_quote": "Jake Stark reviewed the Zone 5 architecture illustrated in arch_zone5_diagram.png",
                    "speaker": "Jake Stark",
                    "timestamp": "05:10",
                    "kind": "technical",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:zone5_stp_pipeline",
                        "type": "system",
                        "name": "Zone 5 STP Pipeline",
                        "description": "Enterprise straight-through processing system",
                    },
                    {
                        "id": "person:jake_stark",
                        "type": "person",
                        "name": "Jake Stark",
                        "description": "PartnerAdvisory LENS Lead",
                    }
                ],
                "relationship_signals": [
                    {
                        "from_id": "person:jake_stark",
                        "to_id": "system:zone5_stp_pipeline",
                        "predicate": "maintains",
                        "_audit_rationale": "Jake Stark maintains Zone 5 straight-through pipeline",
                    }
                ],
                "decision_updates": [
                    {
                        "decision_id": "decision:relax_zone5_threshold",
                        "decision_question": "Should Zone 5 STP threshold be relaxed?",
                        "description": "Relax Zone 5 STP threshold from 95% to 75%",
                        "rationale": "Ratified by engineering leadership based on arch_zone5_diagram.png review",
                        "observed_at": "09/25/2026",
                    }
                ],
                "proposal_updates": [],
                "risk_updates": [],
                "action_updates": [],
            },
        }
        gate3_res = run_qa_gate_3(f3_payload, metadata=self.meeting_meta)
        self.assertTrue(
            gate3_res["gates"]["Gate_3_13_Operator_Thought_Framing_Exclusion"]["passed"],
            f"Gate 3.13 failed: {gate3_res['failures']}",
        )


if __name__ == "__main__":
    unittest.main()
