"""
tests/test_copilot_session_context_packet.py

Unit tests for SessionContextPacket schema, C-19 quarantine sanitization,
serialization, hashing, and formatting for copilot prompt injection.
"""

from __future__ import annotations

import unittest

from dual_pipeline.live_meeting_recorder_core.session_context_packet import (
    SessionContextPacket,
)


class TestSessionContextPacket(unittest.TestCase):
    def test_empty_packet_creation(self):
        packet = SessionContextPacket.empty(project_scope="project_nexus")
        self.assertTrue(packet.is_empty)
        self.assertEqual(packet.project_scope, "project_nexus")
        self.assertEqual(packet.total_items_count, 0)
        self.assertEqual(packet.format_for_copilot_prompt(), "")
        self.assertTrue(len(packet.packet_hash) == 64)

    def test_c19_quarantine_sanitization(self):
        # Items with C-19 markers must be filtered out upon packet creation
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=[
                {"id": "d1", "text": "Deploy Insights Hub lighthouse pilot at Lone Star"},
                {
                    "id": "d2",
                    "text": "Use $1.2M consulting budget as networking_wedge for career_leverage",
                },
            ],
            open_risks=[
                {
                    "id": "r1",
                    "text": "CIP-005 electronic security perimeter violation on live SCADA",
                },
                {"id": "r2", "text": "Internal advisory risk regarding compensation_trajectory"},
            ],
            open_action_items=[
                {"id": "a1", "text": "Align SOW boundaries with harper Hoffman", "assignee": "Amit"},
                {
                    "id": "a2",
                    "text": "Political rationale documentation for INTERNAL_ADVISORY",
                    "assignee": "Anon",
                },
            ],
            recent_assertions=[
                {
                    "id": "ass1",
                    "text": "APEX_TRANS control room uses PI Historian replica",
                    "predicate": "USES",
                },
                {"id": "ass2", "text": "$1.2m engagement milestone", "predicate": "BUDGET"},
            ],
        )

        # C-19 violating items must be pruned
        self.assertEqual(len(packet.open_decisions), 1)
        self.assertEqual(packet.open_decisions[0]["id"], "d1")
        self.assertEqual(len(packet.open_risks), 1)
        self.assertEqual(packet.open_risks[0]["id"], "r1")
        self.assertEqual(len(packet.open_action_items), 1)
        self.assertEqual(packet.open_action_items[0]["id"], "a1")
        self.assertEqual(len(packet.recent_assertions), 1)
        self.assertEqual(packet.recent_assertions[0]["id"], "ass1")
        self.assertEqual(packet.total_items_count, 4)

    def test_serialization_roundtrip(self):
        packet = SessionContextPacket(
            project_scope="project_lens",
            open_decisions=[{"id": "d1", "text": "Phase drone CV charter"}],
            open_risks=[{"id": "r1", "text": "90% accuracy milestone risk"}],
            retrieval_latency_ms=12.5,
        )
        d = packet.to_dict()
        self.assertEqual(d["project_scope"], "project_lens")
        self.assertEqual(d["retrieval_latency_ms"], 12.5)

        restored = SessionContextPacket.from_dict(d)
        self.assertEqual(restored.project_scope, packet.project_scope)
        self.assertEqual(restored.packet_hash, packet.packet_hash)
        self.assertEqual(restored.total_items_count, 2)

    def test_format_for_copilot_prompt(self):
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=[{"text": "Proceed with Lone Star pilot", "subject": "APEX_TRANS"}],
            open_risks=[{"text": "CIP-005 SCADA airgap constraint", "subject": "GridVista"}],
            open_action_items=[{"text": "Schedule orals briefing", "owner": "Drew Oakley"}],
            recent_assertions=[{"text": "ChorusCX handles telephony logging", "predicate": "USES"}],
        )
        formatted = packet.format_for_copilot_prompt(max_chars=2000)
        self.assertIn("[Pre-Fetched Graph Context - Project: apex_trans]", formatted)
        self.assertIn("[DECISION] (APEX_TRANS) Proceed with Lone Star pilot", formatted)
        self.assertIn("[RISK] (GridVista) CIP-005 SCADA airgap constraint", formatted)
        self.assertIn("[ACTION] [Drew Oakley] Schedule orals briefing", formatted)
        self.assertIn("[USES] ChorusCX handles telephony logging", formatted)

    def test_format_respects_max_chars(self):
        long_text = "A" * 500
        packet = SessionContextPacket(
            project_scope="test",
            open_decisions=[{"text": long_text} for _ in range(10)],
        )
        formatted = packet.format_for_copilot_prompt(max_chars=300)
        self.assertLessEqual(len(formatted), 350)
        self.assertIn("truncated", formatted)


if __name__ == "__main__":
    unittest.main()
