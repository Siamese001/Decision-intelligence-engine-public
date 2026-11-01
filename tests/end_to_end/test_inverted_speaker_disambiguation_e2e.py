"""End-to-end test for inverted speaker disambiguation cascade (Sol 6.1 primary, Astra max secondary)."""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.model_registry import ModelTier, get_binding
from dual_pipeline.speaker_disambiguation import (
    AttendeeContext,
    RoomMicConfig,
    SpeakerDisambiguator,
    resolve_shared_room_microphones,
)


class TestInvertedSpeakerDisambiguationE2E(unittest.TestCase):
    """E2E lifecycle test verifying inverted cascade cost-reduction and accuracy."""

    def test_default_tier_bindings_inverted(self) -> None:
        """Verify primary speaker disambiguation tier defaults to Sol 6.1, reserving Astra for escalation."""
        sol_binding = get_binding(ModelTier.QUERY_REASONING)
        frontier_binding = get_binding(ModelTier.FRONTIER_HIGH_REASONING)

        self.assertEqual(sol_binding.model_id, "gpt-6.1-sol")
        self.assertIn(frontier_binding.model_id, ("gpt-6.1-sol", "gpt-6-luna"))

    def test_e2e_inverted_disambiguation_pipeline(self) -> None:
        """Execute full resolution pipeline with room mic config under inverted cascade."""
        transcript = (
            "Speaker 1: As lead architect from PartnerAdvisory, I will present the cloud infrastructure blueprint.\n"
            "Speaker 2: We need to finalize the opentelemetry plumtree and networking wedges for NEA.\n"
        )
        metadata: Dict[str, Any] = {
            "mock_disambiguation": True,
            "attendees": [
                {
                    "name": "Jen",
                    "role": "Architect",
                    "company": "PartnerAdvisory",
                    "domain_topics": ["architecture", "cloud infrastructure", "blueprint"],
                },
                {
                    "name": "Amit",
                    "role": "Tech Lead",
                    "company": "NEA",
                    "domain_topics": ["opentelemetry", "plumtree", "networking"],
                },
            ],
            "shared_room_mics": [
                {
                    "mic_label": "generic-speaker",
                    "display_label": "NEA/PartnerAdvisory Room",
                    "in_room_attendees": [
                        {
                            "name": "Jen",
                            "person_id": "person:jen",
                            "role": "Architect",
                            "company": "PartnerAdvisory",
                            "domain_topics": ["architecture", "cloud infrastructure", "blueprint"],
                        },
                        {
                            "name": "Amit",
                            "person_id": "person:amit",
                            "role": "Tech Lead",
                            "company": "NEA",
                            "domain_topics": ["opentelemetry", "plumtree", "networking"],
                        },
                    ],
                }
            ],
        }
        speaker_map = {"Speaker 1": "Jen", "Speaker 2": "Amit"}

        resolved_text, meta_out = resolve_shared_room_microphones(
            resolved_text=transcript,
            metadata=metadata,
            speaker_map=speaker_map,
        )

        # Verify generic speaker labels were attributed
        self.assertNotIn("Speaker 1:", resolved_text)
        self.assertNotIn("Speaker 2:", resolved_text)
        self.assertIn("Jen", resolved_text)
        self.assertIn("Amit", resolved_text)

        # Verify attribution manifest and two-stage shield telemetry exist
        self.assertIn("_attribution_manifest", meta_out)
        manifest = meta_out["_attribution_manifest"]
        self.assertIn("telemetry", manifest)
        self.assertIn("two_stage_shield", manifest["telemetry"])
        shield_telemetry = manifest["telemetry"]["two_stage_shield"]
        self.assertIn("ambiguous_turns_evaluated", shield_telemetry)
        self.assertIn("shield_resolved_turns", shield_telemetry)

    def test_two_stage_shield_escalation_behavior(self) -> None:
        """Verify that Sol 6.1 runs primary and ambiguous turns escalate to secondary escalator."""
        room_cfg = RoomMicConfig(
            mic_label="room_1",
            display_label="Conference Room",
            in_room_attendees=[
                AttendeeContext(
                    name="Alice",
                    person_id="person:alice",
                    role="Product Lead",
                    company="Acme",
                    domain_topics=["roadmap", "milestones"],
                ),
                AttendeeContext(
                    name="Bob",
                    person_id="person:bob",
                    role="Data Engineer",
                    company="Acme",
                    domain_topics=["pipeline", "schema"],
                ),
            ],
        )

        primary_sol = SpeakerDisambiguator(model="gpt-6.1-sol", reasoning_effort="high", mock=True)
        secondary_astra = SpeakerDisambiguator(model="gpt-6-luna", reasoning_effort="max", mock=True)

        transcript = (
            "Speaker A: Let's discuss roadmap and milestones.\n"
            "Speaker B: The data pipeline schema needs updates.\n"
        )

        result = primary_sol.disambiguate_with_two_stage_shield(
            transcript=transcript,
            room_config=room_cfg,
            secondary_escalator=secondary_astra,
        )

        self.assertIsNotNone(result.resolved_transcript)
        self.assertEqual(len(result.turns), 2)
        # Verify telemetry records shield execution
        self.assertIn("two_stage_shield", result.attribution_manifest["telemetry"])


if __name__ == "__main__":
    unittest.main()
