"""tests/test_meeting_paths_adversarial.py

Adversarial unit tests for meeting paths slug standardization, length budgets, and speaker matching.
"""

from __future__ import annotations

import unittest

from dual_pipeline.meeting_paths import standardize_meeting_slug
from dual_pipeline.speaker_disambiguation_core.disambiguator import SpeakerDisambiguator
from dual_pipeline.speaker_disambiguation_core.models import AttendeeContext, RoomMicConfig


class MeetingPathsAdversarialTests(unittest.TestCase):
    """Adversarial stress tests for meeting paths and speaker disambiguation."""

    def test_very_long_meeting_name_preserves_key_attendees(self) -> None:
        """When combined slug exceeds 40 chars, scope must be truncated first to preserve KeyAttendees."""
        long_name = "Enterprise Cloud Data Migration Architecture Review and Strategy Assessment"
        attendees = ["Marcus Balk", "Amit Ayer"]

        slug = standardize_meeting_slug(long_name, attendees=attendees, max_len=40)
        self.assertLessEqual(len(slug), 40)
        # Suffix must contain KeyAttendees (Marcus_Amit)
        self.assertTrue(slug.endswith("Marcus_Amit"), f"Slug '{slug}' does not preserve KeyAttendees")
        self.assertTrue(slug.startswith("Enterprise_Cloud"), f"Slug '{slug}' scope prefix mangled")

    def test_pure_stop_words_fallback(self) -> None:
        """Titles consisting entirely of stop words must fallback cleanly to 'Sync' or meaningful token."""
        slug1 = standardize_meeting_slug("Discuss and Catch up", attendees=["Jen Pan"])
        self.assertEqual(slug1, "Sync_Jen")

        slug2 = standardize_meeting_slug("Review on and about", attendees=["Marcus Balk", "Jen Pan"])
        self.assertTrue("Sync" in slug2)
        self.assertLessEqual(len(slug2), 40)

    def test_speaker_disambiguation_manifest_multi_delimiter_matching(self) -> None:
        """_build_manifest must match speaker distribution across dashes, brackets, colons, and commas."""
        disambiguator = SpeakerDisambiguator()
        room_config = RoomMicConfig(
            mic_label="Jupiter Conf Room",
            display_label="Jupiter",
            in_room_attendees=[
                AttendeeContext(name="Marcus Balk (Apex Global Enterprises)", person_id="person:marcus_balk", role="Architect", company="Apex Global Enterprises"),
                AttendeeContext(name="Daniel Brankin - Enterprise Architect", person_id="person:daniel_brankin", role="Architect", company="Apex Global Enterprises"),
                AttendeeContext(name="Egle Tamasevice [IBM Contractor]", person_id="person:egle_t", role="Lead", company="IBM"),
            ],
        )
        speaker_distribution = {
            "Marcus Balk": 5,
            "Daniel Brankin": 3,
            "Egle Tamasevice, Tech Lead": 4,
        }
        manifest = disambiguator._build_manifest(
            attributions=[],
            room_config=room_config,
            remote_attendees=[],
            speaker_distribution=speaker_distribution,
        )

        verified = manifest["individually_verified"]
        self.assertIn("person:marcus_balk", verified)
        self.assertIn("person:daniel_brankin", verified)
        self.assertIn("person:egle_t", verified)
        self.assertEqual(len(verified), 3)


if __name__ == "__main__":
    unittest.main()
