"""tests/test_operator_question_firewall.py

Wave 3: Unit test verifying Operator Thought-Framing Question Firewall (Constraint C-29)
across Stage 02/03 prompts, master governance manifest, and Stage U0 ingestion boundary.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


class TestOperatorQuestionFirewall(unittest.TestCase):
    """Verifies that operator thought-framing questions and Copilot telemetry are quarantined."""

    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    def test_c29_registered_in_master_manifest(self) -> None:
        """Asserts C-29 Operator Thought-Framing Question Firewall is in RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md."""
        manifest_p = self.root / "governance" / "decision_intelligence" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"
        self.assertTrue(manifest_p.exists())
        text = manifest_p.read_text(encoding="utf-8")
        self.assertIn("C-29", text)
        self.assertIn("Operator Thought-Framing Question Firewall", text)

    def test_c29_in_stage_02_prompt(self) -> None:
        """Asserts C-29 Operator Thought-Framing Invariant is in 02_executive_recap.md."""
        prompt_p = self.root / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md"
        self.assertTrue(prompt_p.exists())
        text = prompt_p.read_text(encoding="utf-8")
        self.assertIn("Constraint C-29", text)
        self.assertIn("Operator Thought-Framing Invariant", text)

    def test_c29_in_stage_03_prompt(self) -> None:
        """Asserts C-29 Operator Thought-Framing Exclusion is in 03_graph_extractor.md."""
        prompt_p = self.root / "config" / "prompts" / "synthesis" / "stages" / "03_graph_extractor.md"
        self.assertTrue(prompt_p.exists())
        text = prompt_p.read_text(encoding="utf-8")
        self.assertIn("Constraint C-29", text)
        self.assertIn("Operator Thought-Framing Exclusion", text)

    def test_u0_blocks_copilot_feedback_telemetry_ingestion(self) -> None:
        """Asserts Stage U0 raises ValueError when copilot_feedback telemetry is provided as transcript."""
        u0 = U0IngestionBoundary()
        meta = {
            "meeting_name": "Test_Sync",
            "date": "09/25/2026",
            "time": "09:00 AM - 10:00 AM ET",
            "classification": "confidential",
            "input_path": "/tmp/output/01_09-25-2026_test_copilot_feedback.jsonl",
        }
        with self.assertRaises(ValueError) as ctx:
            u0.execute("{\"type\": \"COPILOT_FEEDBACK\"}", meta)
        self.assertIn("copilot_feedback", str(ctx.exception).lower())
        self.assertIn("C-29", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
