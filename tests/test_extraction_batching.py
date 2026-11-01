"""
tests/test_extraction_batching.py

Unit and Acceptance Test Suite for Wave 4:
- Evidence statement batching in Pipeline B (TechArchExtractor, StrategicCommitmentExtractor).
- Compact JSON serialization in SpeakerDisambiguator and AudioOrchestrator.
- Local heuristic pre-filtering in RosterReconcilerAgent.
- Model binding for gpt-5.6-luna (reasoning_effort="high") and gpt-5.6-luna.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.speaker_disambiguation_core.disambiguator import (
    SpeakerDisambiguator,
)
from dual_pipeline.speaker_disambiguation_core.models import (
    AttendeeContext,
    RoomMicConfig,
)
from pipeline_a_intake.agents.roster_reconciler_agent import RosterReconcilerAgent
from pipeline_b_extraction.agents.strategic_commitments_agent import (
    StrategicCommitmentExtractor,
)
from pipeline_b_extraction.agents.tech_arch_agent import TechArchExtractor


class TestExtractionBatching(unittest.IsolatedAsyncioTestCase):
    """Verifies Wave 4 extraction batching, compact JSON serialization, and heuristic filters."""

    async def test_tech_arch_batch_extraction(self) -> None:
        mock_resp = mock.MagicMock()
        mock_resp.choices = [
            mock.MagicMock(
                message=mock.MagicMock(
                    content=json.dumps({
                        "facts": [
                            {
                                "evidence_id": f"ev-{i}",
                                "type": "ARCHITECTURAL_DECISION",
                                "name": f"Architecture Component {i}",
                                "properties": {"component": f"Module {i}"},
                                "strategic_value": 8.5,
                                "tacticality": 3.0,
                            }
                            for i in range(10)
                        ]
                    })
                )
            )
        ]
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create = mock.AsyncMock(return_value=mock_resp)

        agent = TechArchExtractor(
            api_key="sk-proj-test",
            mock_mode=False,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )

        items = [
            {"evidence_id": f"ev-{i}", "speaker": "Alex", "text": f"System decision for module {i}."}
            for i in range(10)
        ]

        task = A2ATask(
            task_id="t-batch-tech",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Extract tech arch batch",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"items": items},
        )

        with mock.patch("config.credentials.CredentialManager.get_async_openai_client", return_value=(mock_client, mock.MagicMock())):
            res = await agent.handle_task(task)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["extracted_count"], 10)
        self.assertEqual(len(res["nodes"]), 10)

        # Must have made exactly ONE call for all 10 items (90% call reduction)
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(call_kwargs["reasoning_effort"], "high")

        # Prompt must contain compact JSON without indentation
        prompt = call_kwargs["messages"][0]["content"]
        self.assertIn('"evidence_id":"ev-0"', prompt)
        self.assertNotIn('\n  "evidence_id"', prompt)

    async def test_strategic_commitments_batch_extraction(self) -> None:
        mock_resp = mock.MagicMock()
        mock_resp.choices = [
            mock.MagicMock(
                message=mock.MagicMock(
                    content=json.dumps({
                        "facts": [
                            {
                                "evidence_id": f"ev-{i}",
                                "type": "DELIVERY_MILESTONE",
                                "name": f"Deliverable {i}",
                                "properties": {"due_date": "06/16/2026"},
                                "strategic_value": 8.0,
                                "tacticality": 4.0,
                            }
                            for i in range(10)
                        ]
                    })
                )
            )
        ]
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create = mock.AsyncMock(return_value=mock_resp)

        agent = StrategicCommitmentExtractor(
            api_key="sk-proj-test",
            mock_mode=False,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )

        items = [
            {"evidence_id": f"ev-{i}", "speaker": "Sarah", "text": f"Delivery commitment for milestone {i}."}
            for i in range(10)
        ]

        task = A2ATask(
            task_id="t-batch-comm",
            parent_task_id=None,
            sender_agent_id="coord",
            recipient_agent_id=agent.card.agent_id,
            objective="Extract commitments batch",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"items": items},
        )

        with mock.patch("config.credentials.CredentialManager.get_async_openai_client", return_value=(mock_client, mock.MagicMock())):
            res = await agent.handle_task(task)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["extracted_count"], 10)
        self.assertEqual(len(res["nodes"]), 10)

        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(call_kwargs["reasoning_effort"], "high")

    def test_disambiguator_compact_json(self) -> None:
        mock_client = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.choices = [
            mock.MagicMock(
                message=mock.MagicMock(content=json.dumps({"attributions": []}))
            )
        ]
        mock_client.chat.completions.create.return_value = mock_resp

        disambiguator = SpeakerDisambiguator(
            client=mock_client,
            model="gpt-5.6-luna",
            reasoning_effort="high",
        )

        raw_transcript = (
            "Alex [00:01:00]: Good morning everyone.\n"
            "Conference Room [00:01:05]: Good morning.\n"
            "Dan [00:01:10]: Let's start the architecture sync.\n"
        )
        # roster not needed
        room_cfg = RoomMicConfig(
            mic_label="Conference Room",
            display_label="Conference Room (Shared)",
            in_room_attendees=[
                AttendeeContext(name="Sarah Connor", person_id="person:sarah", role="Engineer", company="PartnerAdvisory"),
                AttendeeContext(name="Dan Doe", person_id="person:dan", role="Architect", company="PartnerAdvisory"),
            ],
        )

        remote_roster = [{"name": "Alex Smith", "person_id": "person:alex"}]
        disambiguator.disambiguate(raw_transcript, room_cfg, remote_roster)

        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args[1]

        # Verify system and user message formatting
        system_msg = call_kwargs["messages"][0]["content"]
        user_msg = call_kwargs["messages"][1]["content"]

        # Neither message should have "indent=2" formatted JSON
        self.assertNotIn('\n  "Sarah Connor"', system_msg)
        self.assertNotIn('\n    "turn_index"', user_msg)
        # Should contain compact JSON
        self.assertIn('"turn_index":1', user_msg)

    async def test_roster_reconciler_heuristic_pre_filter(self) -> None:
        agent = RosterReconcilerAgent(mock_mode=True)
        roster = ["Alexandre Smith", "Sarah Connor", "Daniel Doe"]

        # 1. Exact match
        res1 = agent._deterministic_match("Sarah Connor", roster)
        self.assertIsNotNone(res1)
        self.assertEqual(res1["canonical_name"], "Sarah Connor")

        # 2. Token overlap (single first name matching multi-part name)
        res2 = agent._deterministic_match("Sarah", roster)
        self.assertIsNotNone(res2)
        self.assertEqual(res2["canonical_name"], "Sarah Connor")

        # 3. Typo / fuzzy sequence similarity ("Sara Conner" -> "Sarah Connor")
        res3 = agent._deterministic_match("Sara Conner", roster)
        self.assertIsNotNone(res3)
        self.assertEqual(res3["canonical_name"], "Sarah Connor")
        self.assertGreaterEqual(res3["confidence"], 0.82)

        # 4. Handle task without LLM call for all resolved
        turns = [
            {"speaker": "Sarah", "text": "Hello"},
            {"speaker": "Sara Conner", "text": "We agree"},
            {"speaker": "Daniel", "text": "Next steps"},
        ]
        task = A2ATask(
            task_id="t-roster",
            parent_task_id=None,
            sender_agent_id="caller",
            recipient_agent_id=agent.card.agent_id,
            objective="Reconcile roster",
            status=A2ATaskStatus.SUBMITTED,
            input_payload={"speaker_turns": turns, "roster_context": roster},
        )
        res = await agent.handle_task(task)
        self.assertEqual(res["status"], "COMPLETED")
        self.assertEqual(res["unresolved_count"], 0)
        self.assertEqual(len(res["resolved_speakers"]), 3)


if __name__ == "__main__":
    unittest.main()
