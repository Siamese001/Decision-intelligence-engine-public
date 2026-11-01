"""End-to-end integration tests for prompt layout reordering and prefix caching optimization."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.llm_judge_core.judges.holistic import call_holistic_llm
from dual_pipeline.llm_judge_core.judges.leverage import AgenticLeverageJudge


class MockCompletionResponse:
    def __init__(self, content: str, model: str = "gpt-5.6-luna"):
        self.id = "mock-resp-id"
        self.model = model
        self.choices = [
            MagicMock(
                message=MagicMock(content=content),
                finish_reason="stop",
            )
        ]
        self.usage = MagicMock(prompt_tokens=200, completion_tokens=50)


class TestPromptCacheLayoutE2E(unittest.TestCase):
    def test_e2e_copilot_system_prompt_invariance(self):
        """Verify copilot system prompt remains 100% identical when graph_context changes."""
        captured_calls = []

        def mock_create(**kwargs):
            captured_calls.append(dict(kwargs))
            return MockCompletionResponse('{"has_guidance": false}')

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        copilot = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Architecture_Review",
            attendees=[{"name": "Amit"}, {"name": "Jane"}],
            mock_mode=False,
            enable_prefetch=False,
        )
        copilot.mock_mode = False
        copilot.disabled = False
        copilot.client = mock_client
        copilot.streaming_handler = None

        # Turn 1: with graph context A
        copilot.record_turn("Amit", "We need to discuss the NERC-CIP compliance requirements.", "10:00:00")
        with patch.object(copilot, "_lookup_graph_context", return_value="NERC-CIP CIP-005 Electronic Security Perimeter"):
            copilot._evaluate_dialogue_sync()

        # Turn 2: with graph context B (different topic)
        copilot.record_turn("Jane", "Let's review the PI Historian replication latency.", "10:01:00")
        with patch.object(copilot, "_lookup_graph_context", return_value="PI Historian Enterprise Interface Specifications"):
            copilot._evaluate_dialogue_sync()

        self.assertEqual(len(captured_calls), 2)
        sys_msg_1 = next(m["content"] for m in captured_calls[0]["messages"] if m["role"] == "system")
        sys_msg_2 = next(m["content"] for m in captured_calls[1]["messages"] if m["role"] == "system")

        # Invariant 1: System prompt is 100% IDENTICAL across changing graph context!
        self.assertEqual(sys_msg_1, sys_msg_2)

        # Invariant 2: Dynamic graph context is safely in user prompt
        user_msg_1 = next(m["content"] for m in captured_calls[0]["messages"] if m["role"] == "user")
        user_msg_2 = next(m["content"] for m in captured_calls[1]["messages"] if m["role"] == "user")
        self.assertIn("NERC-CIP", user_msg_1)
        self.assertIn("PI Historian", user_msg_2)

    def test_e2e_leverage_judge_transcript_prefix(self):
        """Verify leverage judge places stable transcript context before candidate node."""
        captured_calls = []

        def mock_create(**kwargs):
            captured_calls.append(dict(kwargs))
            return MockCompletionResponse('{"pass_gate": true, "political_materiality": 9, "networking_target_identified": true, "is_actionable_wedge": true}')

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        judge = AgenticLeverageJudge(model="gpt-5.6-luna", mock=False)

        transcript = "Forensic meeting dialogue spanning 5000 tokens of discussion..."
        node1 = {"id": "node-1", "title": "Sponsor meeting with VP", "target_stakeholder": "VP Engineering", "political_rationale": "High priority strategic rationale", "networking_wedge": "Actionable wedge for executive engagement"}
        node2 = {"id": "node-2", "title": "Joint POC charter review", "target_stakeholder": "VP Engineering", "political_rationale": "High priority strategic rationale", "networking_wedge": "Actionable wedge for executive engagement"}

        judge.evaluate_agentic_leverage(node1, transcript, client=mock_client, mock=False)
        judge.evaluate_agentic_leverage(node2, transcript, client=mock_client, mock=False)

        self.assertEqual(len(captured_calls), 2)
        user1 = next(m["content"] for m in captured_calls[0]["messages"] if m["role"] == "user")
        user2 = next(m["content"] for m in captured_calls[1]["messages"] if m["role"] == "user")

        # Invariant: Transcript context is positioned FIRST before the candidate node
        self.assertTrue(user1.startswith("MEETING TRANSCRIPT CONTEXT:\nForensic meeting dialogue"))
        self.assertTrue(user2.startswith("MEETING TRANSCRIPT CONTEXT:\nForensic meeting dialogue"))

    def test_e2e_holistic_judge_source_prefix(self):
        """Verify holistic judge places raw source transcript before candidate outputs."""
        captured_calls = []

        def mock_create(**kwargs):
            captured_calls.append(dict(kwargs))
            return MockCompletionResponse('{"overall_verdict": "ACCEPT", "verdict_scores": {"faithfulness": 9}}')

        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create
        mock_recorder = MagicMock()

        call_holistic_llm(
            client=mock_client,
            model="gpt-5.6-luna",
            reasoning_effort="medium",
            f1_text="Output 1 text",
            f2_text="Output 2 text",
            f3_text="Output 3 text",
            source_text="Immutable Raw Source Transcript",
            recorder=mock_recorder,
        )

        self.assertEqual(len(captured_calls), 1)
        user_content = next(m["content"] for m in captured_calls[0]["messages"] if m["role"] == "user")
        # Invariant: Raw source transcript is positioned FIRST
        self.assertTrue(user_content.startswith("RAW SOURCE TRANSCRIPT (REFERENCE):\nImmutable Raw Source Transcript"))


if __name__ == "__main__":
    unittest.main()
