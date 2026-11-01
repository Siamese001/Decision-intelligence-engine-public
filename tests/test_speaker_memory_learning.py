#!/usr/bin/env python3
"""Unit test suite for Wave 3: Cross-Session Speaker & Terminology Memory."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.speaker_disambiguation_core.disambiguator import SpeakerDisambiguator
from dual_pipeline.speaker_disambiguation_core.models import (
    AttendeeContext,
    RoomMicConfig,
)
from dual_pipeline.speaker_memory import SpeakerMemory


class SpeakerMemoryLearningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory_path = Path(self.temp_dir.name) / "speaker_memory.json"
        self.memory = SpeakerMemory(self.memory_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_bootstrap_profiles_loaded(self) -> None:
        """Verify baseline profiles for Reid, Daniel, Sharon, Diana are loaded."""
        reid = self.memory.get_profile("Reid")
        self.assertIsNotNone(reid)
        self.assertEqual(reid.person_id, "person:reid")
        self.assertIn("q4 milestone", reid.domain_topics)

        dan = self.memory.get_profile("Daniel")
        self.assertIsNotNone(dan)
        self.assertEqual(dan.person_id, "person:daniel")

    def test_phonetic_alias_matching(self) -> None:
        """Verify phonetic aliases resolve to the canonical profile."""
        profile_reed = self.memory.get_profile("Reed")
        self.assertIsNotNone(profile_reed)
        self.assertEqual(profile_reed.name, "Reid")

        profile_dan = self.memory.get_profile("Dan")
        self.assertIsNotNone(profile_dan)
        self.assertEqual(profile_dan.name, "Daniel")

    def test_record_attribution_and_cross_session_persistence(self) -> None:
        """Learn a new attendee and verify persistence across session reloads."""
        # Register a new speaker: Kannan (Cloud Architect)
        self.memory.record_attribution(
            name="Kannan",
            utterance="We should deploy the bedrock agentic gateway to handle model routing.",
            topics=["bedrock agentic gateway", "model routing", "anthropic bedrock"],
            person_id="person:kannan",
            persist=True,
        )

        # Confirm immediate in-memory availability
        p = self.memory.get_profile("Kannan")
        self.assertIsNotNone(p)
        self.assertEqual(p.person_id, "person:kannan")
        self.assertIn("bedrock agentic gateway", p.domain_topics)

        # Reload from disk in a fresh SpeakerMemory instance
        reloaded_memory = SpeakerMemory(self.memory_path)
        reloaded_p = reloaded_memory.get_profile("Kannan")
        self.assertIsNotNone(reloaded_p)
        self.assertEqual(reloaded_p.person_id, "person:kannan")
        self.assertIn("bedrock agentic gateway", reloaded_p.domain_topics)

    def test_utterance_scoring_and_disambiguation_integration(self) -> None:
        """Verify SpeakerDisambiguator correctly uses SpeakerMemory for novel attendees."""
        # Register Kannan with domain topics
        self.memory.record_attribution(
            name="Kannan",
            utterance="Deploy model routing on Bedrock",
            topics=["bedrock agentic gateway", "model routing"],
            person_id="person:kannan",
            persist=True,
        )

        # Score utterance directly
        score = self.memory.match_speaker_score(
            "Kannan",
            "Let's review the bedrock agentic gateway configuration today.",
        )
        self.assertGreaterEqual(score, 0.40)

        # Test within SpeakerDisambiguator offline mode
        room_config = RoomMicConfig(
            mic_label="Room-Mic-1",
            display_label="Conference Room A",
            in_room_attendees=[
                AttendeeContext(
                    name="Kannan",
                    person_id="person:kannan",
                    role="Cloud Architect",
                    company="PartnerAdvisory",
                    domain_topics=["bedrock agentic gateway", "model routing"],
                ),
                AttendeeContext(
                    name="Daniel",
                    person_id="person:daniel",
                    role="Engineering Lead",
                    company="APEX",
                    domain_topics=["solar 40"],
                ),
            ],
        )

        # Turn spoken by Room-Mic containing Kannan's domain topics
        room_turns = [
            {
                "line_index": 5,
                "text": "The bedrock agentic gateway is ready for testing.",
                "speaker": "Room-Mic-1",
                "timestamp": "10:02:15",
            }
        ]

        import dual_pipeline.speaker_memory as sm
        orig_mem = sm._GLOBAL_SPEAKER_MEMORY
        try:
            sm._GLOBAL_SPEAKER_MEMORY = self.memory
            disambiguator = SpeakerDisambiguator(mock=True)
            attributions = disambiguator._disambiguate_offline(room_turns, room_config)
            self.assertEqual(len(attributions), 1)
            self.assertEqual(attributions[0].assigned_speaker, "Kannan")
            self.assertEqual(attributions[0].attribution_mode, "DIRECT")
        finally:
            sm._GLOBAL_SPEAKER_MEMORY = orig_mem


if __name__ == "__main__":
    unittest.main()
