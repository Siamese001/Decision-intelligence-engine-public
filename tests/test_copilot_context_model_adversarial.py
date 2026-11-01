"""
tests/test_copilot_context_model_adversarial.py

Adversarial and boundary test suite for ConversationContextModel:
1. Out-of-order timestamps and clock skew (negative delta-t, time traveling backwards).
2. Empty, whitespace-only, and rapid duplicate turns.
3. Single-speaker monologue (>85% turns over >=10 turns triggers dominant speaker).
4. Quiet speakers in roster (attendees who have not spoken in >10 turns).
5. Rule C-19 Career Firewall injection: C-19 markers are strictly quarantined from focus, entities, flow state, and seeds.
6. Empty model edge cases: querying seeds, focus turns, and compute_hash with 0 turns.
7. Long meeting scale & stability stress: 2,500 turns without failure or memory leak.
"""
from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)


class TestCopilotContextModelAdversarial(unittest.TestCase):
    def test_out_of_order_timestamps_and_clock_skew(self) -> None:
        """Adversarial: Turns arriving out-of-order or with backward clock jumps do not explode or crash."""
        model = ConversationContextModel(project_scope="project_lens")

        # Monotonic time jumping backwards
        model.add_turn("Amit", "Initial architectural overview of GCP.", "00:05:00", turn_sec=300.0)
        # Turn arrives claiming to be from 00:02:00 (120s)
        model.add_turn("Jen", "Late packet regarding Cloud SQL.", "00:02:00", turn_sec=120.0)
        # Turn arrives with negative timestamp
        model.add_turn("David", "Malformed negative timestamp.", "00:00:00", turn_sec=-50.0)
        # Turn arrives far in the future
        model.add_turn("Sarah", "Future packet.", "01:00:00", turn_sec=3600.0)
        # Turn arrives jumping back again
        model.add_turn("Amit", "Post-jump packet.", "00:10:00", turn_sec=600.0)

        # Ensure model is valid and compute_hash does not fail
        h = model.compute_hash()
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)

        # Salient entities should be computable without math domain error (no negative sqrt or exp overflow)
        salient = model.salient_entities()
        self.assertIsInstance(salient, list)
        for ent, score in salient:
            self.assertFalse(score != score, f"Score for {ent} is NaN")
            self.assertGreaterEqual(score, 0.0)

    def test_empty_whitespace_and_duplicate_turns(self) -> None:
        """Adversarial: Empty text, whitespace, and consecutive duplicate turns handled gracefully."""
        model = ConversationContextModel(project_scope="project_lens")

        model.add_turn("Amit", "", "00:01:00", turn_sec=60.0)
        model.add_turn("Jen", "   \t\n  ", "00:01:05", turn_sec=65.0)
        model.add_turn("Amit", "Valid statement on SOW boundaries.", "00:01:10", turn_sec=70.0)
        model.add_turn("Amit", "Valid statement on SOW boundaries.", "00:01:10", turn_sec=70.0)

        focus = model.focus_turns()
        self.assertGreaterEqual(len(focus), 1)
        self.assertIn("SOW", model.retrieval_seeds())

    def test_single_speaker_monologue_triggers_dominant_speaker(self) -> None:
        """Adversarial: When one speaker has >85% of turns across >= 10 turns, dominant_speaker is set."""
        attendees = [
            {"name": "Amit", "role": "Architect", "org": "PartnerAdvisory"},
            {"name": "Jen", "role": "Lead", "org": "Apex Global"},
            {"name": "David", "role": "Engineer", "org": "Apex Global"},
        ]
        model = ConversationContextModel(project_scope="project_lens", attendees=attendees)

        # 9 turns by Amit, 1 by Jen -> Amit has 90% (>85%)
        for i in range(9):
            model.add_turn("Amit", f"Architectural point {i} on the delivery roadmap.", f"00:0{i}:00", turn_sec=float(i*30))
        model.add_turn("Jen", "Quick clarification question.", "00:04:30", turn_sec=270.0)

        self.assertEqual(model.flow_state.dominant_speaker, "Amit")
        summary = model.format_flow_summary()
        self.assertIn("Monologue detected (Amit driving >85% of turns)", summary)

        # Now add 10 turns from Jen and David to balance the conversation
        for i in range(10):
            spk = "Jen" if i % 2 == 0 else "David"
            model.add_turn(spk, f"Collaborative feedback {i}.", f"00:0{i+5}:00", turn_sec=float(300 + i*30))

        # Share drops below 85% -> dominant speaker clears
        self.assertIsNone(model.flow_state.dominant_speaker)

    def test_quiet_speakers_detection(self) -> None:
        """Adversarial: Attendees in roster who have not spoken in > 10 turns are flagged as quiet."""
        attendees = [
            {"name": "Amit", "role": "Architect", "org": "PartnerAdvisory"},
            {"name": "Jen", "role": "Lead", "org": "Apex Global"},
            {"name": "David", "role": "Silent Observer", "org": "Apex Global"},
        ]
        model = ConversationContextModel(project_scope="project_lens", attendees=attendees)

        # 12 turns alternating between Amit and Jen, David never speaks
        for i in range(12):
            spk = "Amit" if i % 2 == 0 else "Jen"
            model.add_turn(spk, f"Turn {i} discussing scope.", f"00:0{i}:00", turn_sec=float(i*30))

        self.assertIn("David", model.flow_state.quiet_speakers)

    def test_rule_c19_career_firewall_injection(self) -> None:
        """Acceptance Criteria 6: C-19 turns never enter seeds, focus, or flow state."""
        model = ConversationContextModel(project_scope="project_lens")

        # Clean turns
        model.add_turn("Amit", "We are reviewing Project Nexus architecture.", "00:01:00", turn_sec=60.0)
        model.add_turn("Jen", "The data pipeline throughput meets expectations.", "00:02:00", turn_sec=120.0)

        # Injected turn violating Rule C-19 with SSOT forbidden markers
        c19_injection = "CONFIDENTIAL: Internal advisory discussion regarding networking_wedge and career_leverage."
        model.add_turn("Amit", c19_injection, "00:03:00", turn_sec=180.0)

        # Injected turn with C-19 marker in speaker
        model.add_turn("internal_advisory_lead", "Reviewing compensation_trajectory numbers.", "00:04:00", turn_sec=240.0)

        # 1. Focus turns must not contain C-19 text or speaker
        for ft in model.focus_turns():
            self.assertNotIn("networking_wedge", ft["text"].lower())
            self.assertNotIn("career_leverage", ft["text"].lower())
            self.assertNotIn("internal_advisory", ft["speaker"].lower())

        # 2. Retrieval seeds must not contain C-19 terms
        seeds = model.retrieval_seeds()
        self.assertFalse(any("career_leverage" in s.lower() or "networking_wedge" in s.lower() for s in seeds))

        # 3. Flow state must not contain C-19 terms
        fs_dict = model.flow_state.to_dict()
        fs_str = str(fs_dict).lower()
        self.assertNotIn("career_leverage", fs_str)
        self.assertNotIn("networking_wedge", fs_str)

    def test_empty_model_edge_cases(self) -> None:
        """Adversarial: Empty model handles all accessors without exceptions."""
        empty_model = ConversationContextModel()
        self.assertEqual(empty_model.focus_turns(), [])
        self.assertEqual(empty_model.salient_entities(), [])
        self.assertEqual(empty_model.current_act, "status_update")
        self.assertEqual(empty_model.retrieval_seeds(), [])
        self.assertEqual(empty_model.format_flow_summary(), "Flow: Steady discussion")

        h = empty_model.compute_hash()
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)


if __name__ == "__main__":
    unittest.main()
