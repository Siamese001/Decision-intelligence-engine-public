"""tests/test_pipeline_c_healer.py

Acceptance test suite for Wave 4: Pipeline C Retrieval Perspective Healer (Agent-C4-Healer).
Verifies:
1. Agent Card declaration, model binding (gpt-5.6-sol reasoning_effort=high).
2. Surgical repair of perspective starvation via graph ontology expansion.
3. A2A task protocol handling.
"""
from __future__ import annotations

import asyncio
import unittest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
    get_retrieval_perspective_healer_card,
)


class TestPipelineCHealer(unittest.TestCase):
    """Verifies Pipeline C Retrieval Perspective Healer under gpt-5.6-sol."""

    def test_healer_card_declaration(self) -> None:
        card = get_retrieval_perspective_healer_card()
        self.assertEqual(card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(card.model_binding.reasoning_effort, ("medium", "high", "max"))
        self.assertEqual(card.role, "Retrieval Perspective Healer")
        self.assertIn("HealPerspectiveStarvation", card.skills)
        self.assertIn("BridgeReleaseGateCitations", card.skills)

    def test_heal_starved_perspective_mock(self) -> None:
        healer = RetrievalPerspectiveHealerAgent(mock_mode=True)
        res = asyncio.run(
            healer.heal_starved_perspective(
                query="vector database latency",
                starved_lens="TECHNICAL",
                active_ontology_terms=["system:bge-m3", "system:sqlite-fts5"],
            )
        )
        self.assertTrue(res.is_success)
        self.assertIn("system:bge-m3", res.healed_artifact.get("expanded_terms", []))

    def test_a2a_task_handling(self) -> None:
        healer = RetrievalPerspectiveHealerAgent(mock_mode=True)
        task = A2ATask(
            task_id="heal-c-task-1",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:caller",
            recipient_agent_id=healer.card.agent_id,
            objective="Heal starved perspective query",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "query": "commercial SLA penalties",
                "starved_lens": "COMMERCIAL",
                "ontology_terms": ["workstream:sow-phase1"],
            },
        )

        res = asyncio.run(healer.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertIn("healed_artifact", res)


if __name__ == "__main__":
    unittest.main()
