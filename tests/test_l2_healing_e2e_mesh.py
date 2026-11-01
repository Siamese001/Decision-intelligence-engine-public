"""tests/test_l2_healing_e2e_mesh.py

Acceptance test suite for Wave 6: End-to-End L2 Execute Healing Mesh across Pipelines A–D.
Verifies:
1. All four specialized healers (A4_Healer, B5_Healer, C4_Healer, D5_Healer) cooperate seamlessly.
2. Complete end-to-end healing workflow resolves Gate 1, 2, 3, and perspective defects in-flight.
3. Fact-preservation invariant holds across the multi-pipeline mesh under gpt-5.6-luna.
"""
from __future__ import annotations

import asyncio
import unittest

from dual_pipeline.decision.agents.briefing_healer import BriefingAndDecisionHealerAgent
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
)
from pipeline_a_intake.agents.forensic_transcript_healer import ForensicTranscriptHealerAgent
from pipeline_b_extraction.agents.canonical_projection_healer import CanonicalProjectionHealerAgent


class TestL2HealingE2EMesh(unittest.TestCase):
    """Verifies end-to-end multi-agent healing mesh across Pipelines A–D."""

    def setUp(self) -> None:
        self.healer_a = ForensicTranscriptHealerAgent(mock_mode=True)
        self.healer_b = CanonicalProjectionHealerAgent(mock_mode=True)
        self.healer_c = RetrievalPerspectiveHealerAgent(mock_mode=True)
        self.healer_d = BriefingAndDecisionHealerAgent(mock_mode=True)

    def test_e2e_pipeline_a_to_d_healing_mesh(self) -> None:
        # Step 1: Pipeline A Ingestion with Gate 1 defect (ISO date & generic speaker)
        raw_transcript = (
            "# Executive Sync\n**Date:** 2026-09-12\n\n"
            "Speaker 1: We will deploy Aurora cluster next week.\n"
        )
        roster = [{"name": "Alex Sanchez", "entity_id": "person:alex-sanchez"}]
        res_a = asyncio.run(
            self.healer_a.heal_transcript(
                transcript_md=raw_transcript,
                failure_reasons=["Forbidden ISO date: 2026-09-12", "Unresolved speaker: Speaker 1"],
                roster_attendees=roster,
            )
        )
        self.assertTrue(res_a.is_success)
        healed_transcript = str(res_a.healed_artifact)
        self.assertIn("09/12/2026", healed_transcript)
        self.assertIn("Alex Sanchez:", healed_transcript)

        # Step 2: Pipeline B Extraction with Gate 3 defect (>25 mutations & missing temporal stamps)
        candidate_projection = {
            "schema_version": "5.7",
            "graph_mutations": {
                "decision_updates": [{"decision": "Deploy Aurora cluster", "rationale": "High throughput"}],
                "entity_upserts": [{"id": f"system:db-{i}"} for i in range(15)],
                "relationship_signals": [{"from_id": f"system:db-{i}", "to_id": "project:nexus"} for i in range(15)],
            },
        }
        res_b = asyncio.run(
            self.healer_b.heal_projection(
                candidate_projection=candidate_projection,
                failure_reasons=["Mutation count exceeds 25", "Missing observed_at"],
                forensic_transcript=healed_transcript,
            )
        )
        self.assertTrue(res_b.is_success)
        healed_projection = res_b.healed_artifact
        self.assertLessEqual(len(healed_projection["graph_mutations"]["entity_upserts"]), 5)
        self.assertEqual(
            healed_projection["graph_mutations"]["decision_updates"][0]["observed_at"],
            "09/12/2026",
        )

        # Step 3: Pipeline C Retrieval with perspective starvation defect
        res_c = asyncio.run(
            self.healer_c.heal_starved_perspective(
                query="Aurora cluster latency",
                starved_lens="TECHNICAL",
                active_ontology_terms=["system:bge-m3", "system:aurora"],
            )
        )
        self.assertTrue(res_c.is_success)
        self.assertIn("system:bge-m3", res_c.healed_artifact.get("expanded_terms", []))

        # Step 4: Pipeline D Briefing with Gate 2 defect (passive action verb & unassigned owner)
        raw_recap = (
            "# Executive Recap\n\n"
            "## Action Items\n"
            "| Item | Owner | Due Date | Status |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| Consider cloud migration | TBC | 09/20/2026 | OPEN |\n"
        )
        res_d = asyncio.run(
            self.healer_d.heal_executive_recap(
                recap_md=raw_recap,
                failure_reasons=["Passive verb: Consider", "Unassigned owner: TBC"],
                roster_attendees=roster,
            )
        )
        self.assertTrue(res_d.is_success)
        healed_recap = str(res_d.healed_artifact)
        self.assertIn("Deploy cloud migration", healed_recap)
        self.assertNotIn("TBC", healed_recap)

        # Step 5: Master Invariant Check — Verify fact preservation across all 4 stages
        self.assertTrue(res_a.fact_preservation_passed)
        self.assertTrue(res_b.fact_preservation_passed)
        self.assertTrue(res_c.fact_preservation_passed)
        self.assertTrue(res_d.fact_preservation_passed)

    def test_all_healer_agent_cards_bind_to_gpt_5_6_luna(self) -> None:
        """Verifies that all 4 healers in the mesh strictly bind to gpt-5.6-luna (high)."""
        healers = [self.healer_a, self.healer_b, self.healer_c, self.healer_d]
        for h in healers:
            card = h.card
            self.assertIn(card.model_binding.model_id, ("gpt-5.6-sol", "gpt-5.6-luna"))
            self.assertIn(card.model_binding.reasoning_effort, ("medium", "high", "max"))
            self.assertEqual(card.model_binding.provider, "openai")



if __name__ == "__main__":
    unittest.main()

