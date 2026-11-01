"""End-to-end tests for judge contract evaluation and archetype binding."""
import unittest
from typing import Any, Dict

from dual_pipeline.llm_judge_core.contracts.judge_criterion_contract import (
    validate_output1_scorecard,
    validate_output2_scorecard,
    validate_output3_scorecard,
)
from dual_pipeline.llm_judge_core.judges import (
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
)
from dual_pipeline.llm_judge_core.judges.recap import _apply_defect_penalties, _finalize_envelope
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype


class TestJudgeContractE2E(unittest.TestCase):
    """Verifies end-to-end evaluation flows under the unified judge contract."""

    def test_forensic_judge_mock_evaluation_e2e(self) -> None:
        """Verify ForensicTranscriptJudge in mock mode produces a compliant scorecard."""
        judge = ForensicTranscriptJudge()
        f1_text = (
            "[00:00:10] Speaker A: Good morning everyone, let us begin.\n"
            "[00:00:25] Speaker B: Good morning. We have deployed the patch.\n"
            "[00:00:40] Speaker A: Excellent, metrics look stable."
        )
        source_text = f1_text
        metadata = {
            "meeting_name": "Deployment Sync",
            "attendees": ["Speaker A", "Speaker B"],
            "attendee_count": 2,
        }
        res = judge.evaluate(f1_text, source_text, mock=True, metadata=metadata)
        self.assertIn("verdict", res)
        self.assertIn("composite_score", res)
        val_res = validate_output1_scorecard(res)
        self.assertTrue(val_res.is_valid, f"Validation failed: {val_res.errors}")

    def test_forensic_judge_speaker_collapse_detection_e2e(self) -> None:
        """Verify single-speaker collapse (>85% turns in multi-attendee session) triggers defect penalty."""
        judge = ForensicTranscriptJudge()
        # 11 turns where Speaker A speaks 10 (90.9%) in a multi-attendee meeting
        turns = [
            f"[00:0{i}:00] Speaker A: Update number {i} regarding progress."
            for i in range(10)
        ]
        turns.append("[00:10:00] Speaker B: Understood.")
        f1_collapsed = "\n".join(turns)

        metadata = {
            "meeting_name": "Multi-Attendee Strategy",
            "attendees": ["Speaker A", "Speaker B", "Speaker C"],
            "attendee_count": 3,
        }
        res = judge.evaluate(f1_collapsed, f1_collapsed, mock=True, metadata=metadata)
        # Verify attribution score was penalized
        dim2 = res.get("dimension_2_attribution_and_room_mic_hygiene", 25)
        self.assertLessEqual(dim2, 15)
        self.assertTrue(res.get("single_speaker_collapse", False))

    def test_recap_judge_mock_evaluation_e2e(self) -> None:
        """Verify ExecutiveRecapJudge in mock mode produces section scores matching composite."""
        judge = ExecutiveRecapJudge()
        f1_text = (
            "[00:00:10] John: Let us review the pipeline rollout.\n"
            "[00:00:25] Alice: Pipeline migration completed with zero data loss.\n"
            "[00:00:40] John: Great work, Alice."
        )
        f2_text = (
            "# Executive Recap\n\n"
            "## Decisions\n- Rollout approved.\n\n"
            "## Key Updates & Technical Substance\n- Pipeline migration achieved zero loss.\n\n"
            "## Action & Governance Ledger\n| DRI | Task | Timing |\n| Alice | Monitor | EOD |\n"
        )
        res = judge.evaluate(f2_text, f1_text, mock=True)
        self.assertIn("section_scores", res)
        self.assertIn("composite_score", res)
        val_res = validate_output2_scorecard(res)
        self.assertTrue(val_res.is_valid, f"Output 2 validation errors: {val_res.errors}")

    def test_recap_judge_source_bound_archetype_e2e(self) -> None:
        """Verify candidate edits cannot change the evaluated meeting archetype."""
        judge = ExecutiveRecapJudge()
        # Source text indicates a client discovery session
        source_discovery = "Speaker 1: Welcome to the client discovery interview with Apex Global."
        # Candidate text attempts to inject architecture deep dive headers
        candidate_with_fake_headers = (
            "# Architecture Deep Dive Technical Review\n\n"
            "## Technical Architecture Deep Dive\n- Attempted override"
        )
        # Evaluate without explicit archetype
        res = judge.evaluate(candidate_with_fake_headers, source_discovery, mock=True)
        # Effective archetype should match discovery, not architecture deep dive
        self.assertIn(res.get("meeting_archetype"), ["STRATEGIC_DISCOVERY", "GENERAL"])
        self.assertNotEqual(res.get("meeting_archetype"), "ARCHITECTURE_DEEP_DIVE")

    def test_recap_critique_envelope_targets_deficient_section(self) -> None:
        """Verify critique envelope targets the specific deficient section and never defaults to action ledger."""
        parsed = {
            "composite_score": 80,
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 20,  # lost 15 points
                "conditional_extensions": 20,
                "appendix_stakeholder_perspectives": 30,  # lost 5 points
            },
            "dimension_1_executive_altitude": 20,
            "dimension_2_technical_substance": 20,
            "dimension_3_strategic_synthesis": 20,
            "dimension_4_scoping_and_modality": 20,
            "forensic_rationale": "Missing depth in technical updates.",
        }
        envelope = _finalize_envelope(parsed)
        critique = envelope.get("critique_diff_envelope", {})
        target = critique.get("target_path", "")
        # Must target the section with largest loss (key_updates_technical_substance -> Key Discussion Points & Findings)
        self.assertIn("Key Discussion Points", target)
        self.assertNotIn("Action & Governance Ledger", target)

    def test_graph_judge_mock_evaluation_e2e(self) -> None:
        """Verify GraphExtractionJudge in mock mode uses canonical MeetingArchetype."""
        judge = GraphExtractionJudge(mock=True)
        f3_data = {
            "nodes": [
                {"id": "entity:1", "name": "Pipeline", "type": "System"},
                {"id": "decision:1", "name": "Deploy", "type": "Decision"},
            ],
            "edges": [],
        }
        f1_text = "Speaker 1: We conducted an architectural deep dive and code walkthrough of SCADA telemetry."
        res = judge.evaluate(f3_data, f1_text, mock=True)
        self.assertIn("verdict", res)
        self.assertIn("composite_score", res)
        # Should resolve to canonical ARCHITECTURE_DEEP_DIVE
        self.assertEqual(res.get("meeting_archetype"), MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value)

    def test_decomposed_multi_judge_coordination(self) -> None:
        """Verify DecomposedMultiJudge coordinates micro-judges cleanly."""
        multi_judge = DecomposedMultiJudge(mock=True)
        self.assertIsNotNone(multi_judge.judge_forensic)
        self.assertIsNotNone(multi_judge.judge_recap)
        self.assertIsNotNone(multi_judge.judge_graph)


if __name__ == "__main__":
    unittest.main()
