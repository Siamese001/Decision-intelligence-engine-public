"""tests/test_pipeline_d_healer.py

Acceptance test suite for Wave 5: Pipeline D Briefing & Decision Healer (Agent-D5-Healer).
Verifies:
1. Agent Card declaration, model binding (gpt-5.6-sol reasoning_effort=high).
2. Surgical repair of Gate 2 defects (passive action verbs, unassigned TBC owners).
3. A2A task protocol handling.
"""
from __future__ import annotations

import asyncio
import unittest

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.decision.agents.briefing_healer import (
    BriefingAndDecisionHealerAgent,
    get_briefing_healer_card,
)


class TestPipelineDHealer(unittest.TestCase):
    """Verifies Pipeline D Briefing & Decision Healer under gpt-5.6-sol."""

    def test_healer_card_declaration(self) -> None:
        card = get_briefing_healer_card()
        self.assertEqual(card.model_binding.model_id, "gpt-5.6-sol")
        self.assertIn(card.model_binding.reasoning_effort, ("medium", "high", "max"))

        self.assertEqual(card.role, "Briefing and Decision Healer")
        self.assertIn("HealPassiveActionVerbs", card.skills)
        self.assertIn("AssignRosterOwners", card.skills)

    def test_heal_executive_recap_mock(self) -> None:
        healer = BriefingAndDecisionHealerAgent(mock_mode=True)
        raw_recap = (
            "# Executive Briefing: Project Nexus Sync\n\n"
            "**Date:** 09/12/2026\n\n"
            "## Actionable Next Steps\n"
            "| Item | Owner | Due Date | Status |\n"
            "| :--- | :--- | :--- | :--- |\n"
            "| Consider cloud migration | TBC | 09/20/2026 | OPEN |\n"
            "| Explore vector database | Alex Sanchez | 09/22/2026 | OPEN |\n"
        )
        failures = [
            "Passive verb in action item: 'Consider cloud migration'",
            "Unassigned owner in action item: 'TBC'",
        ]
        roster = [{"name": "PartnerAdvisory Engineering Lead", "entity_id": "person:partner_advisory-lead"}]

        res = asyncio.run(
            healer.heal_executive_recap(
                recap_md=raw_recap,
                failure_reasons=failures,
                roster_attendees=roster,
            )
        )

        self.assertTrue(res.is_success)
        healed_str = str(res.healed_artifact)
        self.assertIn("Deploy cloud migration", healed_str)
        self.assertIn("Implement vector database", healed_str)
        self.assertNotIn("TBC", healed_str)

    def test_a2a_task_handling(self) -> None:
        healer = BriefingAndDecisionHealerAgent(mock_mode=True)
        task = A2ATask(
            task_id="heal-d-task-1",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:caller",
            recipient_agent_id=healer.card.agent_id,
            objective="Heal action table in executive recap",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={
                "recap_md": "| Consider running validation | TBC |",
                "failure_reasons": ["Passive verb", "Unassigned owner"],
                "roster_context": [],
            },
        )

        res = asyncio.run(healer.handle_task(task))
        self.assertEqual(res["status"], "COMPLETED")
        self.assertIn("healed_recap_md", res)


if __name__ == "__main__":
    unittest.main()
