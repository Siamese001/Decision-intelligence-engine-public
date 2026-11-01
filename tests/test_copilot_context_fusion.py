"""
tests/test_copilot_context_fusion.py

Unit tests for Wave 3 Copilot Context Fusion:
Verifies that SessionContextPacket is correctly fused with zero-latency inverted-index context
in LiveCopilotAdvisor, adheres to character/token caps, and enriches both Track 1 and Track 2.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever
from dual_pipeline.live_meeting_recorder_core.session_context_packet import SessionContextPacket


class TestCopilotContextFusion(unittest.TestCase):
    def setUp(self):
        self.mock_nodes = {
            "node_neet": {
                "canonical_name": "APEX_TRANS",
                "display_name": "Apex Energy Transmission",
                "node_type": "organization",
            },
            "node_scada": {
                "canonical_name": "SCADA",
                "display_name": "SCADA EMS Network",
                "node_type": "system",
            },
        }
        self.mock_assertions = {
            "ass_scada": {
                "subject_node_id": "node_scada",
                "predicate": "CONSTRAINS",
                "object_literal": "CIP-005 airgap perimeter",
                "canonical_status": "ACTIVE",
            }
        }
        self.mock_topology = SimpleNamespace(
            nodes=self.mock_nodes,
            assertions=self.mock_assertions,
            forward_adjacency={},
            reverse_adjacency={},
        )

    def test_advisor_initializes_with_provided_packet(self):
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=[{"text": "Proceed with Lone Star pilot"}],
            open_risks=[{"text": "CIP-005 firewall compliance"}],
        )
        advisor = LiveCopilotAdvisor(
            project_name="apex_trans",
            mock_mode=True,
            session_context_packet=packet,
            enable_prefetch=False,
        )
        self.assertEqual(advisor.session_context_packet.project_scope, "apex_trans")
        self.assertEqual(len(advisor.session_context_packet.open_decisions), 1)

    def test_lookup_graph_context_fuses_packet_with_dialogue(self):
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=[{"text": "Phase charter into grounded-contract spike", "subject": "APEX_TRANS"}],
            open_risks=[{"text": "SCADA ingress risk", "subject": "GridVista"}],
        )
        advisor = LiveCopilotAdvisor(
            project_name="apex_trans",
            mock_mode=True,
            session_context_packet=packet,
            enable_prefetch=False,
        )
        advisor.in_memory_graph = self.mock_topology
        advisor.retriever = CopilotGraphRetriever(self.mock_topology)

        # Dialogue mentions SCADA -> triggers inverted-index retrieval AND includes packet
        fused = advisor._lookup_graph_context("What is our architecture approach for SCADA data?")
        self.assertIn("Verified Graph Knowledge:", fused)
        self.assertIn("Entity: SCADA", fused)
        self.assertIn("[Pre-Fetched Graph Context - Project: apex_trans]", fused)
        self.assertIn("[DECISION] (APEX_TRANS) Phase charter into grounded-contract spike", fused)
        self.assertIn("[RISK] (GridVista) SCADA ingress risk", fused)

    def test_lookup_graph_context_with_unmatched_dialogue_still_provides_packet(self):
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=[{"text": "Phase charter into grounded-contract spike"}],
        )
        advisor = LiveCopilotAdvisor(
            project_name="apex_trans",
            mock_mode=True,
            session_context_packet=packet,
            enable_prefetch=False,
        )
        advisor.in_memory_graph = self.mock_topology
        advisor.retriever = CopilotGraphRetriever(self.mock_topology)

        # Dialogue does NOT match any graph entities
        fused = advisor._lookup_graph_context("Good morning everyone, let's look at the calendar.")
        self.assertNotIn("Verified Graph Knowledge:", fused)
        self.assertIn("[Pre-Fetched Graph Context - Project: apex_trans]", fused)
        self.assertIn("Phase charter into grounded-contract spike", fused)

    def test_combined_context_capped_at_budget(self):
        huge_decisions = [{"text": f"Decision {i}: " + ("x" * 200)} for i in range(25)]
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=huge_decisions,
        )
        advisor = LiveCopilotAdvisor(
            project_name="apex_trans",
            mock_mode=True,
            session_context_packet=packet,
            enable_prefetch=False,
        )
        fused = advisor._lookup_graph_context("SCADA discussions")
        self.assertLessEqual(len(fused), 3600)

    def test_mock_evaluation_outputs_fused_graph_context(self):
        packet = SessionContextPacket(
            project_scope="apex_trans",
            open_decisions=[{"text": "Lone Star pilot approval"}],
        )
        advisor = LiveCopilotAdvisor(
            project_name="apex_trans",
            mock_mode=True,
            session_context_packet=packet,
            enable_prefetch=False,
        )
        for i in range(4):
            advisor.record_turn(
                speaker="Mandy",
                text=f"Turn {i} discussing Lone Star lighthouse pilot rollout and timeline.",
                timestamp=f"[00:{i:02d}]",
            )

        feedback = advisor.evaluate_live_turns(current_time=10.0)
        self.assertIsNotNone(feedback)
        self.assertIn("graph_context", feedback)
        self.assertIn("Lone Star pilot approval", feedback["graph_context"])


if __name__ == "__main__":
    unittest.main()
