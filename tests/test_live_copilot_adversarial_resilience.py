"""
tests/test_live_copilot_adversarial_resilience.py

Adversarial resilience tests for Wave 1 live copilot mode:
1. Token budget verification (max_completion_tokens >= 1200, zero truncation).
2. Priority turn queuing & preemption (no silent turn drops on strategic inflections).
3. In-memory graph token-overlap search (<15ms latency).
4. Conversational speaker tracking (vocative address, self-identification, turn alternation).
5. Unpunctuated modal question inflection detection.
"""

import time
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.inflection_detector import (
    InflectionDetector,
    InflectionTriggerType,
)
from dual_pipeline.live_meeting_recorder_core.stream_aggregator import (
    ConversationalSpeakerTracker,
)


class TestLiveCopilotAdversarialResilience(unittest.TestCase):
    """Test suite for Wave 1 live copilot mode resilience."""

    def test_copilot_token_budget_is_at_least_1200(self):
        """Verify that copilot does not truncate reasoning models with a 250-token budget."""
        advisor = LiveCopilotAdvisor(mock_mode=True)
        advisor._recent_turns = [
            {"timestamp": "[00:10]", "speaker": "Jake", "text": "Can you elaborate on the architecture split?"}
        ]

        with patch.object(advisor, "client", create=True) as mock_client:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock(message=MagicMock(content='{"has_guidance": false}'))]
            mock_client.chat.completions.create.return_value = mock_resp
            advisor.mock_mode = False

            advisor._evaluate_dialogue_sync(None)

            self.assertTrue(mock_client.chat.completions.create.called)
            call_kwargs = mock_client.chat.completions.create.call_args[1]
            self.assertGreaterEqual(call_kwargs.get("max_completion_tokens", 0), 1200)

    def test_copilot_priority_turn_queuing(self):
        """Verify that priority inflection events are queued rather than silently dropped when worker is busy."""
        advisor = LiveCopilotAdvisor(mock_mode=True, cooldown_seconds=10.0)

        feedback_received = []

        def on_feedback(f):
            feedback_received.append(f)

        advisor.submit_turn_async(
            speaker="Jake",
            text="What are your thoughts on the database?",
            timestamp="[00:10]",
            current_time=100.0,
            on_feedback=on_feedback,
        )

        priority_event = MagicMock()
        priority_event.is_priority = True
        priority_event.reason = "Question hand-off"
        priority_event.cue_text = "Amit, do you agree?"

        accepted = advisor.submit_turn_async(
            speaker="Jake",
            text="Amit, do you agree with this design?",
            timestamp="[00:12]",
            current_time=101.0,
            on_feedback=on_feedback,
            inflection_event=priority_event,
        )
        self.assertTrue(accepted)
        advisor.shutdown(wait=True)

    def test_in_memory_graph_token_overlap_search(self):
        """Verify in-memory graph search matches entities via token overlap in <15ms."""
        advisor = LiveCopilotAdvisor(mock_mode=True)
        mock_graph = MagicMock()
        mock_graph.nodes = {
            "system:power_bi_gateway": {
                "canonical_name": "Power BI Gateway",
                "display_name": "Enterprise Reporting Gateway",
                "node_type": "system",
            },
            "system:bge_m3": {
                "canonical_name": "BGE-M3",
                "display_name": "Dense Vector Embeddings",
                "node_type": "system",
            },
        }
        mock_graph.forward_adjacency = {
            "system:power_bi_gateway": [
                {"relationship_family": "INTEGRATES_WITH", "to_node_name": "Nexus Hub", "edge_type": "system"}
            ]
        }
        advisor.in_memory_graph = mock_graph

        t0 = time.perf_counter()
        ctx = advisor._lookup_graph_context("How will the reporting gateway integrate with our stack?")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        self.assertLess(elapsed_ms, 15.0)
        self.assertIn("Power BI Gateway", ctx)
        self.assertIn("Nexus Hub", ctx)

    def test_conversational_speaker_tracker_vocatives(self):
        """Verify vocatives ('Amit, what do you think') attributes speaker to counter-party."""
        attendees = [
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
            {"name": "Jake Stark", "role": "Client Director"},
        ]
        tracker = ConversationalSpeakerTracker(attendees=attendees, primary_speaker="Amit Ayer")

        speaker = tracker.attribute_chunk("Amit, what do you think about the timeline?")
        self.assertEqual(speaker, "Jake Stark")

        responding_speaker = tracker.attribute_chunk("I think we should do a grounded-contract spike first.")
        self.assertEqual(responding_speaker, "Amit Ayer")

    def test_conversational_speaker_tracker_self_identification(self):
        """Verify self-identification ('This is Jake') attributes correctly."""
        attendees = [
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect"},
            {"name": "Jake Stark", "role": "Client Director"},
        ]
        tracker = ConversationalSpeakerTracker(attendees=attendees, primary_speaker="Amit Ayer")

        speaker = tracker.attribute_chunk("Hey everyone, this is Jake from the client team.")
        self.assertEqual(speaker, "Jake Stark")

    def test_inflection_detector_unpunctuated_modal_question(self):
        """Verify unpunctuated modal questions trigger STOPPING_POINT_QUESTION."""
        detector = InflectionDetector()

        event = detector.evaluate_chunk(
            chunk_idx=1,
            sec=8.0,
            text="Can you clarify the integration boundary for the gateway",
            rms=0.05,
            current_time=time.time(),
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_type, InflectionTriggerType.STOPPING_POINT_QUESTION)
        self.assertTrue(event.is_priority)

    def test_inflection_detector_conversational_cue(self):
        """Verify 'what's your take' triggers STOPPING_POINT_QUESTION even without punctuation."""
        detector = InflectionDetector()

        event = detector.evaluate_chunk(
            chunk_idx=2,
            sec=16.0,
            text="So that's the high level design tell me what your thoughts are",
            rms=0.06,
            current_time=time.time(),
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.trigger_type, InflectionTriggerType.STOPPING_POINT_QUESTION)


if __name__ == "__main__":
    unittest.main()
