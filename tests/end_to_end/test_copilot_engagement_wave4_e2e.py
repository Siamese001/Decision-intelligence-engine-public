"""
tests/end_to_end/test_copilot_engagement_wave4_e2e.py

Wave 4 E2E lifecycle acceptance tests:
Verifies full end-to-end copilot clause grounding, sentence verification,
streaming displayed_bytes contracts, summary validation, and fail-closed
empty evidence/timeout handling.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.copilot_clause_verifier import (
    GROUNDING_QUESTION_FALLBACK,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    validate_copilot_feedback,
)
from dual_pipeline.live_meeting_recorder_core.streaming_copilot import (
    StreamingCopilotHandler,
)


class MockStreamChunk:
    def __init__(self, content: str) -> None:
        self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]


class MockCompletions:
    def __init__(self, chunks: List[str]) -> None:
        self._chunks = chunks

    def create(self, **kwargs: Any) -> List[MockStreamChunk]:
        return [MockStreamChunk(c) for c in self._chunks]


class MockChat:
    def __init__(self, chunks: List[str]) -> None:
        self.completions = MockCompletions(chunks)


class MockOpenAIClient:
    def __init__(self, chunks: List[str]) -> None:
        self.chat = MockChat(chunks)


class TestCopilotEngagementWave4E2E(unittest.TestCase):
    def setUp(self) -> None:
        self.jarvis_sso_asrt: Dict[str, Any] = {
            "assertion_id": "asrt_jarvis_sso_10",
            "predicate": "DEPENDS_ON",
            "subject_node_id": "project:jarvis",
            "object_literal": "Single Sign-On",
            "claim_text": "Jarvis depends on Single Sign-On for authentication.",
            "engagement_id": "project:jarvis",
        }
        self.jarvis_db_asrt: Dict[str, Any] = {
            "assertion_id": "asrt_jarvis_db_11",
            "predicate": "USES",
            "subject_node_id": "project:jarvis",
            "object_literal": "Spanner",
            "claim_text": "Jarvis uses Google Cloud Spanner for relational storage.",
            "engagement_id": "project:jarvis",
        }
        self.assertions = [self.jarvis_sso_asrt, self.jarvis_db_asrt]

    def test_e2e_grounded_answer_lifecycle(self) -> None:
        """Full lifecycle of a verified factual answer with supporting bullets and summaries."""
        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Jarvis depends on Single Sign-On for authentication.",
            "supporting_details_bullets": [
                "Jarvis depends on Single Sign-On for authentication.",
                "Jarvis uses Google Cloud Spanner for relational storage.",
            ],
            "speaker_summary": "Jarvis depends on Single Sign-On for authentication.",
            "headline": "Authentication Architecture",
        }

        validated = validate_copilot_feedback(
            candidate,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )

        self.assertIsNotNone(validated)
        self.assertEqual(validated["label"], "Verified Answer")
        self.assertFalse(validated["is_suggestion"])
        self.assertEqual(validated["rigor_tier"], "VERIFIED")
        self.assertIn("asrt_jarvis_sso_10", validated["cited_assertion_ids"])
        self.assertIn("asrt_jarvis_db_11", validated["cited_assertion_ids"])
        self.assertEqual(len(validated["supporting_details_bullets"]), 2)
        self.assertIn("Single Sign-On", validated["speaker_summary"])

    def test_e2e_mixed_sentences_filtration(self) -> None:
        """Verifies that ungrounded clauses are dropped while grounded clauses survive."""
        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": (
                "Jarvis depends on Single Sign-On for authentication. "
                "SecOps security reviews require a two-week turnaround."
            ),
            "supporting_details_bullets": [
                "Jarvis depends on Single Sign-On for authentication.",
                "SecOps security reviews take two weeks to complete.",
            ],
            "speaker_summary": (
                "Jarvis depends on Single Sign-On for authentication. "
                "SecOps reviews require two weeks."
            ),
        }

        validated = validate_copilot_feedback(
            candidate,
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )

        self.assertIsNotNone(validated)
        # Grounded sentence is kept
        self.assertIn("Jarvis depends on Single Sign-On for authentication.", validated["suggested_script"])
        # Ungrounded sentence is stripped
        self.assertNotIn("two-week", validated["suggested_script"])
        self.assertNotIn("two weeks", validated["suggested_script"])

        # Bullets check
        self.assertEqual(len(validated["supporting_details_bullets"]), 1)
        self.assertIn("Single Sign-On", validated["supporting_details_bullets"][0])

        # Summary check
        self.assertNotIn("two weeks", validated["speaker_summary"])
        self.assertEqual(validated["cited_assertion_ids"], ["asrt_jarvis_sso_10"])
        self.assertEqual(validated["label"], "Verified Answer")

    def test_e2e_streaming_evaluation_contract(self) -> None:
        """Verifies streaming execution with token deltas and displayed_bytes match."""
        raw_json_chunks = [
            '{"has_guidance": true, ',
            '"category": "TECHNICAL", ',
            '"suggested_script": "Jarvis depends on Single Sign-On for authentication.", ',
            '"supporting_details_bullets": ["Jarvis depends on Single Sign-On for authentication."]}'
        ]
        mock_client = MockOpenAIClient(raw_json_chunks)
        handler = StreamingCopilotHandler(client=mock_client, model_id="gpt-5.6-astra")

        tokens_received: List[str] = []
        result = handler.execute_streaming_evaluation(
            call_kwargs={"messages": []},
            on_token=lambda tok: tokens_received.append(tok),
            verified_assertions=self.assertions,
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )

        self.assertIsNotNone(result)
        self.assertEqual(len(tokens_received), 4)
        self.assertEqual(result["track"], "TRACK_2_DEEP_REASONING")
        self.assertEqual(result["label"], "Verified Answer")
        self.assertEqual(result["displayed_bytes"], result["suggested_script"])
        self.assertEqual(result["suggested_script"], "Jarvis depends on Single Sign-On for authentication.")

    def test_e2e_empty_evidence_and_timeout_fail_closed(self) -> None:
        """When evidence is empty or times out, factual claims fail closed into clarifications."""
        candidate = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Jarvis uses Google Cloud Spanner for relational storage.",
            "supporting_details_bullets": ["Relational storage on Spanner."],
            "speaker_summary": "Jarvis uses Spanner.",
        }

        # Empty evidence simulating retrieval timeout or 0 returned assertions
        validated = validate_copilot_feedback(
            candidate,
            verified_assertions=[],
            rigor_tier="VERIFIED",
            engagement_id="project:jarvis",
        )

        self.assertIsNotNone(validated)
        self.assertEqual(validated["rigor_tier"], "UNVERIFIED")
        self.assertEqual(validated["cited_assertion_ids"], [])
        self.assertEqual(validated["supporting_details_bullets"], [])
        self.assertEqual(validated["suggested_script"], GROUNDING_QUESTION_FALLBACK)
        self.assertEqual(validated["label"], "Clarification")
        self.assertTrue(validated["is_suggestion"])


if __name__ == "__main__":
    unittest.main()
