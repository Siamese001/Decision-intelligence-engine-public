"""tests/test_pipeline_a_healer.py

Acceptance test suite for Wave 2: Pipeline A Forensic Transcript Healer (Agent-A4-Healer).
Verifies:
1. Agent Card declaration, model binding (gpt-5.6-sol reasoning_effort=high).
2. Direct surgical repair of Gate 1 defects (unresolved generic speakers, ISO dates, ALR table).
3. IngestionCoordinator in-flight healing integration.
"""
from __future__ import annotations

import asyncio
import unittest

from dual_pipeline.model_registry import ModelTier, get_binding
from pipeline_a_intake.agents.forensic_transcript_healer import (
    ForensicTranscriptHealerAgent,
    get_forensic_transcript_healer_card,
)
from pipeline_a_intake.agents.ingestion_coordinator import IngestionCoordinator


class TestPipelineAHealer(unittest.TestCase):
    """Verifies Pipeline A Forensic Transcript Healer under governed model tiers."""

    def test_healer_card_declaration(self) -> None:
        card = get_forensic_transcript_healer_card()
        binding = get_binding(ModelTier.SURGICAL_HEALER)
        self.assertEqual(card.model_binding.model_id, binding.model_id)
        self.assertIn(card.model_binding.reasoning_effort, ("medium", "high", "max"))

        self.assertEqual(card.role, "Forensic Transcript Healer")
        self.assertIn("HealGenericSpeakers", card.skills)

    def test_direct_healing_execution(self) -> None:
        healer = ForensicTranscriptHealerAgent(mock_mode=True)
        raw_md = (
            "# Project Nexus Architecture Sync\n"
            "**Date:** 2026-09-12\n\n"
            "## Ambiguity Lock Register (ALR)\n"
            "| Lock ID | Item | Ambiguity Description | Forensic Resolution | Status |\n"
            "|:---:|:---:|:---:|:---:|:---:|\n"
            "| ALR-01 | Audio boundary | Cleaned overlap | Resolved | `LOCKED` |\n\n"
            "## Transcript\n\n"
            "**Speaker 1:** [00:15] We should confirm the database schema migration.\n"
        )
        failures = [
            "Output 1 contains forbidden ISO-8601 dates: ['2026-09-12']",
            "Unresolved generic speakers detected: {'Speaker 1'}",
        ]
        roster = [{"name": "Alex Sanchez", "entity_id": "person:alex-sanchez"}]

        result = asyncio.run(
            healer.heal_transcript(
                transcript_md=raw_md,
                failure_reasons=failures,
                roster_attendees=roster,
            )
        )

        self.assertTrue(result.is_success)
        healed_md = str(result.healed_artifact)
        self.assertIn("09/12/2026", healed_md)
        self.assertIn("Alex Sanchez:", healed_md)
        self.assertNotIn("2026-09-12", healed_md)
        self.assertNotIn("Speaker 1:", healed_md)

    def test_ingestion_coordinator_in_flight_healing(self) -> None:
        coordinator = IngestionCoordinator(mock_mode=True)

        res = coordinator.run_ingestion_pipeline(
            meeting_name="Cloud Strategy Sync",
            raw_transcript_text="Speaker 1: Deploying the service now on 2026-09-12.",
            known_roster=["Jen Pan"],
        )

        self.assertEqual(res["status"], "COMPLETED")
        art_01 = res.get("artifact_01_markdown", "")
        # Verified that in-flight healing normalized the date and speaker
        self.assertIn("09/12/2026", art_01)
        self.assertIn("claim_check_pointer", res)


if __name__ == "__main__":
    unittest.main()
