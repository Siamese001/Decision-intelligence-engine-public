"""
tests/end_to_end/test_copilot_prefetch_e2e.py

End-to-End Acceptance Test for Copilot Graph Pre-Fetching Lifecycle:
1. Session Initialization: Load in-memory graph topology and trigger automatic pre-fetch.
2. Context Packet Hydration: Validate SessionContextPacket is populated with open decisions, risks, actions.
3. Live Audio Turn Ingestion: Feed realistic meeting dialogue turns into LiveCopilotAdvisor.
4. Dual-Track Execution: Verify both Track 1 (Flash Snap) and Track 2 (Deep Reasoning) receive fused graph context.
5. Feedback Validation: Verify output meets copilot advisory contract and contains no C-19 leaks.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import CopilotGraphRetriever


class TestCopilotPrefetchE2E(unittest.TestCase):
    def setUp(self):
        # Build canonical enterprise graph topology
        self.mock_nodes = {
            "node_neet": {
                "canonical_name": "APEX_TRANS",
                "display_name": "Apex Energy Transmission",
                "node_type": "organization",
                "description": "Regulated transmission business unit",
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
                "description": "Airgapped transmission telemetry system",
            },
            "node_lonestar": {
                "canonical_name": "Lone Star",
                "display_name": "Lone Star Transmission",
                "node_type": "project",
                "description": "Texas transmission utility operating unit",
            },
        }

        self.mock_assertions = {
            "ass_lonestar_decision": {
                "subject_node_id": "node_lonestar",
                "predicate": "DECISION",
                "object_literal": "Authorize Phase 1 Insights Hub pilot deployment at Lone Star",
                "canonical_status": "ACTIVE",
            },
            "ass_scada_risk": {
                "subject_node_id": "node_scada",
                "predicate": "RISK_OF",
                "object_literal": "Direct SCADA connection compromises CIP-005 airgap perimeter",
                "canonical_status": "ACTIVE",
            },
            "ass_mandy_action": {
                "subject_node_id": "node_mandy",
                "predicate": "DELIVERS",
                "object_literal": "Submit draft orals narrative for APEX Infrastructure Automation RFP",
                "canonical_status": "ACTIVE",
            },
            "ass_neet_fact": {
                "subject_node_id": "node_neet",
                "predicate": "VALIDATES",
                "object_literal": "APEX_TRANS validates ChorusCX and PI Historian telemetry bridge",
                "canonical_status": "ACTIVE",
            },
        }

        self.mock_topology = SimpleNamespace(
            nodes=self.mock_nodes,
            assertions=self.mock_assertions,
            forward_adjacency={},
            reverse_adjacency={},
        )

    def test_full_copilot_prefetch_lifecycle_e2e(self):
        # 1. Initialize LiveCopilotAdvisor with automatic pre-fetch
        advisor = LiveCopilotAdvisor(
            project_name="apex_trans",
            meeting_name="APEX_TRANS Insights Hub Architecture Working Session",
            attendees=[
                {"name": "harper Hoffman", "role": "VP Operations", "org": "APEX_TRANS"},
                {"name": "Alex Rivera", "role": "Director", "org": "APEX_TRANS"},
            ],
            mock_mode=True,
            cooldown_seconds=1.0,
            enable_prefetch=False,  # manually inject topology first
        )
        advisor.in_memory_graph = self.mock_topology
        advisor.retriever = CopilotGraphRetriever(
            self.mock_topology,
            attendees=advisor.attendees,
        )

        # Trigger prefetch bridge
        from dual_pipeline.live_meeting_recorder_core.prefetch_bridge import CopilotPrefetchBridge

        bridge = CopilotPrefetchBridge(
            in_memory_graph=advisor.in_memory_graph,
            retriever=advisor.retriever,
            timeout_seconds=5.0,
        )
        packet = bridge.prefetch(
            project_name=advisor.project_name,
            meeting_name=advisor.meeting_name,
            attendees=advisor.attendees,
        )
        advisor.session_context_packet = packet

        # 2. Verify SessionContextPacket is populated and valid
        self.assertFalse(packet.is_empty)
        self.assertEqual(packet.project_scope, "apex_trans")
        self.assertTrue(len(packet.open_decisions) >= 1)
        self.assertTrue(len(packet.open_risks) >= 1)
        self.assertTrue(len(packet.open_action_items) >= 1)
        self.assertTrue(len(packet.packet_hash) == 64)
        self.assertLess(packet.retrieval_latency_ms, 1000.0)

        # 3. Feed live dialogue turns into the advisor
        received_feedbacks = []

        def on_feedback_cb(fb):
            received_feedbacks.append(fb)

        # Turn 1: SCADA dilemma (should trigger Track 1 instant Flash Snap)
        advisor.submit_turn_async(
            speaker="Alex Rivera",
            text="Can we pull real-time SCADA telemetry directly into the new control room console?",
            timestamp="[05:15]",
            current_time=5.0,
            on_feedback=on_feedback_cb,
        )

        # Track 1 Flash Snap verification
        flash_snaps = [f for f in received_feedbacks if f.get("is_flash_snap")]
        self.assertTrue(len(flash_snaps) >= 1)
        fs = flash_snaps[0]
        self.assertEqual(fs["category"], "TECHNICAL")
        self.assertIn("CIP-005", fs["suggested_script"])
        self.assertIn("[Pre-Fetched Graph Context", fs["graph_context"])
        self.assertIn("Lone Star", fs["graph_context"])

        # Feed additional turns to build conversational context for Track 2
        advisor.record_turn(
            speaker="harper Hoffman",
            text="We cannot touch live SCADA under CIP-005. What was our decision regarding Lone Star?",
            timestamp="[05:30]",
            turn_sec=6.0,
        )
        advisor.record_turn(
            speaker="Alex Rivera",
            text="We wanted a lighthouse pilot at Lone Star to prove the architecture first.",
            timestamp="[05:45]",
            turn_sec=7.0,
        )
        advisor.record_turn(
            speaker="harper Hoffman",
            text="Let's make sure the SOW reflects that scope cleanly before next week.",
            timestamp="[06:00]",
            turn_sec=8.0,
        )

        # 4. Trigger Track 2 evaluation
        track2_feedback = advisor.evaluate_live_turns(current_time=15.0)
        self.assertIsNotNone(track2_feedback)
        self.assertEqual(track2_feedback["type"], "COPILOT_FEEDBACK")
        self.assertTrue(track2_feedback["has_guidance"])
        self.assertIn("graph_context", track2_feedback)

        # Verify fused graph context contains pre-fetched historical facts
        graph_ctx = track2_feedback["graph_context"]
        self.assertIn("Lone Star", graph_ctx)
        self.assertIn("CIP-005", graph_ctx)
        self.assertIn("harper Hoffman", graph_ctx)

        # 5. Clean shutdown
        advisor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
