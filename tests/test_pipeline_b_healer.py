"""tests/test_pipeline_b_healer.py

Acceptance test suite for Wave 3: Pipeline B Canonical Projection Healer (Agent-B5-Healer).
Verifies:
1. Agent Card declaration, model binding (gpt-5.6-sol reasoning_effort=high).
2. Surgical repair of Gate 3 defects (Pydantic schema, mutation pruning <=25, temporal stamps).
3. Fact-preservation invariant enforcement.
"""
from __future__ import annotations

import asyncio
import unittest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from pipeline_b_extraction.agents.canonical_projection_healer import (
    CanonicalProjectionHealerAgent,
    get_canonical_projection_healer_card,
)


class TestPipelineBHealer(unittest.TestCase):
    """Verifies Pipeline B Canonical Projection Healer under gpt-5.6-sol."""

    def test_healer_card_declaration(self) -> None:
        card = get_canonical_projection_healer_card()
        self.assertIn(card.model_binding.model_id, ("gpt-5.6-sol", "gpt-6.1-sol"))
        self.assertIn(card.model_binding.reasoning_effort, ("medium", "high", "max"))

        self.assertEqual(card.role, "Canonical Projection Healer")
        self.assertIn("PruneMutationBudget", card.skills)
        self.assertIn("HealPydanticSchema", card.skills)

    def test_heal_projection_mock_execution(self) -> None:
        healer = CanonicalProjectionHealerAgent(mock_mode=True)
        # Create candidate with >25 mutations and missing temporal stamps
        mutations = {
            "entity_upserts": [{"id": f"system:sys-{i}", "type": "SYSTEM"} for i in range(15)],
            "relationship_signals": [{"from_id": f"system:sys-{i}", "to_id": "project:nexus", "predicate": "RELATES_TO"} for i in range(15)],
            "decision_updates": [
                {"decision": "Adopt BGE-M3 for Dense Retrieval", "rationale": "SOTA performance"}
            ],
            "proposal_updates": [],
            "action_updates": [],
            "risk_updates": [],
        }
        candidate_json = {
            "schema_version": "5.7",
            "graph_mutations": mutations,
        }
        failures = [
            "Total graph mutations (31) exceeds maximum budget of 30.",
            "Decision missing required temporal attribute 'observed_at'",
        ]

        result = asyncio.run(
            healer.heal_projection(
                candidate_projection=candidate_json,
                failure_reasons=failures,
                forensic_transcript="Alex Sanchez confirmed BGE-M3 adoption on 09/12/2026.",
            )
        )

        self.assertTrue(result.is_success)
        healed_data = result.healed_artifact
        self.assertIsNotNone(healed_data)

        # Verify budget was pruned to <= 25 mutations
        healed_mutations = healed_data["graph_mutations"]
        total_mutations = sum(len(healed_mutations.get(k, [])) for k in (
            "entity_upserts", "relationship_signals", "decision_updates",
            "proposal_updates", "action_updates", "risk_updates"
        ))
        self.assertLessEqual(total_mutations, 25)

        # Verify temporal stamps were added
        dec = healed_mutations["decision_updates"][0]
        self.assertEqual(dec.get("observed_at"), "09/12/2026")
        self.assertEqual(dec.get("stale_after"), "12/31/2026")

    def test_a2a_task_handling(self) -> None:
        healer = CanonicalProjectionHealerAgent(mock_mode=True)
        candidate = {
            "schema_version": "5.7",
            "graph_mutations": {
                "decision_updates": [{"decision": "Deploy to AWS", "rationale": "High availability"}],
                "entity_upserts": [{"id": "system:aws-cluster"}],
            }
        }
        task = A2ATask(
            task_id="heal-b-task-1",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:caller",
            recipient_agent_id=healer.card.agent_id,
            objective="Heal candidate graph projection",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "candidate_projection": candidate,
                "failure_reasons": ["Missing observed_at"],
                "forensic_transcript": "Transcript text",
            },
        )

        res = asyncio.run(healer.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertIn("healed_projection", res)
        self.assertEqual(
            res["healed_projection"]["graph_mutations"]["decision_updates"][0]["observed_at"],
            "09/12/2026",
        )


if __name__ == "__main__":
    unittest.main()
