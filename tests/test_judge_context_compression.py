"""
tests/test_judge_context_compression.py

Unit test suite verifying Wave 3 Synthesis & Decomposed Judge Context Compression
for gpt-5.6-luna (reasoning_effort="high") and gpt-5.6-luna.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from dual_pipeline.llm_judge_core import (
    AgenticLeverageJudge,
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
    GraphExtractionJudge,
    HolisticReleaseJudge,
)


class TestJudgeContextCompression(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_client = mock.MagicMock()

    def _setup_mock_completion(self, json_payload: dict):
        mock_resp = mock.MagicMock()
        mock_resp.choices = [
            mock.MagicMock(
                message=mock.MagicMock(content=json.dumps(json_payload)),
                finish_reason="stop",
            )
        ]
        mock_resp.usage = mock.MagicMock(
            prompt_tokens=4000,
            completion_tokens=500,
            total_tokens=4500,
            prompt_tokens_details=mock.MagicMock(cached_tokens=2500),
            completion_tokens_details=mock.MagicMock(reasoning_tokens=200),
        )
        self.mock_client.chat.completions.create.return_value = mock_resp

    def test_forensic_judge_message_structure_and_bounding(self) -> None:
        self._setup_mock_completion({
            "dimension_1_forensic_grounding_and_fidelity": 24,
            "dimension_2_attribution_and_room_mic_hygiene": 24,
            "dimension_3_chronological_and_timestamp_integrity": 25,
            "dimension_4_asr_cleanup_and_domain_preservation": 24,
            "composite_score": 97,
            "verdict": "PASS",
            "forensic_rationale": "Grounded.",
            "actionable_remediation_diff": "",
            "grounding_citations": [],
        })

        judge = ForensicTranscriptJudge(model="gpt-5.6-luna", reasoning_effort="high")
        alr_header = "# Ambiguity Lock Register\n| Term / Acoustic Artifact | Resolution | Type | Confirmed Speaker |\n| :--- | :--- | :--- | :--- |\n| none | none | none | Alex Rivera |\n\n"
        large_f1 = alr_header + ("**Alex Rivera (PartnerAdvisory)** [00:01:00]: Discussion of enterprise architecture.\n**Jane Doe (APEX)** [00:01:30]: Enterprise architecture aligned.\n" * 500)
        large_source = "**Alex Rivera (PartnerAdvisory)** [00:01:00]: Raw source discussion of architecture.\n**Jane Doe (APEX)** [00:01:30]: Enterprise architecture aligned.\n" * 500

        res = judge.evaluate(
            f1_text=large_f1,
            raw_source_text=large_source,
            client=self.mock_client,
            mock=False,
        )
        self.assertEqual(res["verdict"], "PASS")

        # Verify dispatched kwargs
        self.mock_client.chat.completions.create.assert_called_once()
        kwargs = self.mock_client.chat.completions.create.call_args[1]
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["reasoning_effort"], "high")

        # Must have exactly 2 messages: system (cached rubric) and user (bounded context)
        messages = kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Forensic Dialogue & Transcript Auditor", messages[0]["content"])

    def test_recap_judge_message_structure_and_bounding(self) -> None:
        self._setup_mock_completion({
            "dimension_1_executive_altitude": 24,
            "dimension_2_technical_substance": 24,
            "dimension_3_strategic_synthesis": 24,
            "dimension_4_scoping_and_modality": 24,
            "composite_score": 96,
            "verdict": "PASS",
            "forensic_rationale": "High altitude executive recap.",
            "actionable_remediation_diff": "",
            "grounding_citations": [],
        })

        judge = ExecutiveRecapJudge(model="gpt-5.6-luna", reasoning_effort="high")
        f1 = "Speaker 1: Strategic recap ground truth context.\n" * 800
        f2 = "# Executive Recap\n\nDate: 09/12/2026\n\nContent..."

        res = judge.evaluate(
            f2_text=f2,
            f1_text=f1,
            client=self.mock_client,
            mock=False,
        )
        self.assertEqual(res["verdict"], "PASS")

        kwargs = self.mock_client.chat.completions.create.call_args[1]
        messages = kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Senior Executive Advisor", messages[0]["content"])

    def test_graph_judge_message_structure_and_bounding(self) -> None:
        self._setup_mock_completion({
            "_reasoning": "Coherent graph without conversational fragments.",
            "dimension_1_strategic_edge": 14,
            "dimension_2_knowledge_layer_density": 14,
            "dimension_3_semantic_atomicity": 19,
            "dimension_4_predicate_specificity": 14,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 19,
            "composite_score": 95,
            "verdict": "PASS",
            "forensic_rationale": "Valid graph.",
            "actionable_remediation_diff": "",
            "grounding_citations": [],
        })

        judge = GraphExtractionJudge(model="gpt-5.6-luna", reasoning_effort="high", mock=False)
        f1 = "Speaker 1: Graph source context.\n" * 800
        f3 = '{"signals": [{"from_id": "person:alice", "to_id": "system:luna", "relationship": "OWNS"}]}'

        res = judge.evaluate(
            f3_text=f3,
            f1_text=f1,
            client=self.mock_client,
            mock=False,
        )
        self.assertEqual(res["verdict"], "PASS")

        kwargs = self.mock_client.chat.completions.create.call_args[1]
        messages = kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Knowledge Graph Master Architect", messages[0]["content"])

    def test_leverage_judge_message_structure_and_compact_json(self) -> None:
        self._setup_mock_completion({
            "is_actionable_wedge": True,
            "political_materiality": 9,
            "networking_target_identified": True,
            "pass_gate": True,
            "rejection_reason": None,
        })

        judge = AgenticLeverageJudge(model="gpt-5.6-luna", reasoning_effort="high", mock=False)
        node = {
            "node_type": "INFO_ASYMMETRY",
            "key_players": ["Sarah", "Dan"],
            "political_rationale": "Timeline misalignment.",
        }
        transcript = "Speaker 1: Discussion of timeline slips.\n" * 500

        res = judge.evaluate_agentic_leverage(
            node=node,
            transcript_text=transcript,
            client=self.mock_client,
            mock=False,
        )
        self.assertTrue(res.pass_gate)

        kwargs = self.mock_client.chat.completions.create.call_args[1]
        messages = kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        # User message must contain compact JSON without 2-space formatting
        self.assertIn('"node_type":"INFO_ASYMMETRY"', messages[1]["content"])

    def test_holistic_judge_message_structure_and_bounding(self) -> None:
        self._setup_mock_completion({
            "_reasoning": "Holistic evaluation across all 3 artifacts passed.",
            "output_1_score": 95,
            "output_2_score": 95,
            "output_3_score": 95,
            "cross_artifact_integrity_score": 95,
            "composite_score": 95,
            "verdict": "PASS",
            "output_1_verdict": "PASS",
            "output_2_verdict": "PASS",
            "output_3_verdict": "PASS",
            "forensic_rationale": "All passed.",
            "actionable_remediation_diff": "",
            "grounding_citations": [],
        })

        judge = HolisticReleaseJudge(model="gpt-5.6-luna", reasoning_effort="high", mock=False)
        judge.reconciler.reconcile = mock.MagicMock(return_value={"passed": True, "status": "PASS", "reconciled_signals": []})
        alr_header = "# Ambiguity Lock Register\n| Term / Acoustic Artifact | Resolution | Type | Confirmed Speaker |\n| :--- | :--- | :--- | :--- |\n| none | none | none | Alex Rivera |\n\n"
        large_f1 = alr_header + ("**Alex Rivera (PartnerAdvisory)** [00:01:00]: Output 1 text.\n**Jane Doe (APEX)** [00:01:30]: Response text.\n" * 500)
        large_f2 = "Output 2 text.\n" * 500
        large_f3 = '{"signals": []}\n' * 500
        large_src = "Raw source text.\n" * 1000

        res = judge.evaluate(
            output_1_text=large_f1,
            output_2_text=large_f2,
            output_3_text=large_f3,
            raw_transcript=large_src,
            client=self.mock_client,
            mock=False,
        )
        self.assertEqual(res["status"], "PASS")

        kwargs = self.mock_client.chat.completions.create.call_args[1]
        messages = kwargs["messages"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Release Evaluation Judge", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
