"""
tests/test_copilot_prefetch_bridge.py

Unit tests for CopilotPrefetchBridge: bounded graph pre-fetching at session start,
categorization of decisions/risks/action items, C-19 quarantine, and timeout budget enforcement.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    prefetch_session_packet,
)
from dual_pipeline.live_meeting_recorder_core.prefetch_bridge import CopilotPrefetchBridge
from dual_pipeline.live_meeting_recorder_core.session_context_packet import SessionContextPacket


class TestCopilotPrefetchBridge(unittest.TestCase):
    def setUp(self):
        # Build synthetic in-memory graph
        self.mock_nodes = {
            "node_neet": {
                "canonical_name": "APEX_TRANS",
                "display_name": "Apex Energy Transmission",
                "node_type": "organization",
                "description": "Transmission operating unit",
            },
            "node_mandy": {
                "canonical_name": "harper Hoffman",
                "display_name": "harper Hoffman",
                "node_type": "person",
                "description": "VP of Operations at APEX_TRANS",
            },
            "node_scada": {
                "canonical_name": "SCADA",
                "display_name": "SCADA EMS Network",
                "node_type": "system",
                "description": "Airgapped telemetry network",
            },
            "node_c19": {
                "canonical_name": "Internal Advisory Note",
                "display_name": "Political rationale for $1.2M",
                "node_type": "document",
                "description": "career_leverage advisory note",
            },
        }

        self.mock_assertions = {
            "ass_dec_1": {
                "subject_node_id": "node_neet",
                "predicate": "DECISION",
                "object_literal": "Deploy Insights Hub lighthouse pilot at Lone Star in Q4",
                "canonical_status": "ACTIVE",
            },
            "ass_risk_1": {
                "subject_node_id": "node_scada",
                "predicate": "RISK_OF",
                "object_literal": "CIP-005 electronic security perimeter boundary violation",
                "canonical_status": "ACTIVE",
            },
            "ass_action_1": {
                "subject_node_id": "node_mandy",
                "predicate": "DELIVERS",
                "object_literal": "Deliver draft ROCC playbook by Friday",
                "canonical_status": "ACTIVE",
            },
            "ass_fact_1": {
                "subject_node_id": "node_neet",
                "predicate": "VALIDATES",
                "object_literal": "APEX_TRANS validates PI Historian replica architecture",
                "canonical_status": "ACTIVE",
            },
            "ass_c19": {
                "subject_node_id": "node_c19",
                "predicate": "DECISION",
                "object_literal": "Allocate $1.2M budget as career_leverage",
                "canonical_status": "ACTIVE",
            },
        }

        self.mock_topology = SimpleNamespace(
            nodes=self.mock_nodes,
            assertions=self.mock_assertions,
            forward_adjacency={},
            reverse_adjacency={},
        )
        self.retriever = CopilotGraphRetriever(
            self.mock_topology,
            attendees=[{"name": "harper Hoffman", "role": "VP", "org": "APEX_TRANS"}],
        )

    def test_prefetch_with_mock_graph_populates_packet(self):
        bridge = CopilotPrefetchBridge(
            in_memory_graph=self.mock_topology,
            retriever=self.retriever,
            timeout_seconds=5.0,
        )
        packet = bridge.prefetch(
            project_name="apex_trans",
            meeting_name="APEX_TRANS Insights Hub Architecture",
            attendees=[{"name": "harper Hoffman", "role": "VP", "org": "APEX_TRANS"}],
        )

        self.assertFalse(packet.is_empty)
        self.assertEqual(packet.project_scope, "apex_trans")
        self.assertGreater(packet.retrieval_latency_ms, 0.0)
        self.assertLess(packet.retrieval_latency_ms, 1000.0)

        # Check categorizations
        self.assertTrue(len(packet.open_decisions) >= 1)
        self.assertIn("Lone Star", packet.open_decisions[0]["text"])

        self.assertTrue(len(packet.open_risks) >= 1)
        self.assertIn("CIP-005", packet.open_risks[0]["text"])

        self.assertTrue(len(packet.open_action_items) >= 1)
        self.assertIn("ROCC playbook", packet.open_action_items[0]["text"])

        # Check prompt formatting
        formatted = packet.format_for_copilot_prompt()
        self.assertIn("[DECISION]", formatted)
        self.assertIn("[RISK]", formatted)
        self.assertIn("[ACTION]", formatted)

    def test_prefetch_c19_quarantine_enforcement(self):
        bridge = CopilotPrefetchBridge(
            in_memory_graph=self.mock_topology,
            retriever=self.retriever,
            timeout_seconds=5.0,
        )
        packet = bridge.prefetch(
            project_name="apex_trans",
            meeting_name="General Sync",
        )

        formatted = packet.format_for_copilot_prompt()
        self.assertNotIn("$1.2M", formatted)
        self.assertNotIn("career_leverage", formatted)
        self.assertNotIn("Political rationale", formatted)

    def test_prefetch_with_none_graph_returns_empty_packet(self):
        bridge = CopilotPrefetchBridge(
            in_memory_graph=None,
            retriever=None,
            timeout_seconds=5.0,
        )
        packet = bridge.prefetch(project_name="project_lens")
        self.assertTrue(packet.is_empty)
        self.assertEqual(packet.project_scope, "project_lens")

    def test_prefetch_timeout_budget_graceful(self):
        bridge = CopilotPrefetchBridge(
            in_memory_graph=self.mock_topology,
            retriever=self.retriever,
            timeout_seconds=0.000001,  # near-zero timeout
        )
        packet = bridge.prefetch(project_name="apex_trans")
        self.assertIsInstance(packet, SessionContextPacket)

    def test_prefetch_session_packet_convenience_function(self):
        packet = prefetch_session_packet(
            in_memory_graph=self.mock_topology,
            retriever=self.retriever,
            project_name="apex_trans",
            meeting_name="APEX_TRANS Insights Hub Architecture",
            attendees=[{"name": "harper Hoffman", "role": "VP", "org": "APEX_TRANS"}],
        )
        self.assertFalse(packet.is_empty)
        self.assertEqual(packet.project_scope, "apex_trans")


if __name__ == "__main__":
    unittest.main()
