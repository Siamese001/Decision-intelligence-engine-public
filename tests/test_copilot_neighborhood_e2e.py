"""
tests/test_copilot_neighborhood_e2e.py

End-to-end integration test for Copilot Bounded Multi-Hop Neighborhoods (Wave 3).
Tests the complete lifecycle:
1. Session prefetch via CopilotPrefetchBridge extracts 2-hop connected chains.
2. SessionContextPacket serializes and formats [CONNECTED] multi-hop chains.
3. CopilotGraphRetriever.retrieve_context enriches entity context with precomputed chains in < 1 ms.
4. LiveCopilotAdvisor integrates precomputed neighborhood context into live advisory generation.
"""
from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from typing import Any, Dict

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import ConversationContextModel
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
    SessionContextPacket,
)


def _build_e2e_enterprise_graph() -> SimpleNamespace:
    nodes: Dict[str, Dict[str, Any]] = {
        "person:mandy_hopmann": {
            "canonical_name": "harper Hoffman",
            "display_name": "harper Hoffman",
            "node_type": "person",
            "organization": "APEX_TRANS",
        },
        "org:apex_trans": {
            "canonical_name": "Apex Energy Transmission",
            "display_name": "APEX_TRANS",
            "node_type": "organization",
            "organization": "Apex Global",
        },
        "project:nexus": {
            "canonical_name": "Project Nexus",
            "display_name": "Project Nexus Core",
            "node_type": "project",
            "organization": "Apex Global",
        },
        "workstream:scada_integration": {
            "canonical_name": "SCADA Integration Workstream",
            "display_name": "SCADA Integration",
            "node_type": "workstream",
            "organization": "APEX_TRANS",
        },
        "system:lone_star_ems": {
            "canonical_name": "Lone Star EMS",
            "display_name": "Lone Star Control Center EMS",
            "node_type": "system",
            "organization": "APEX_TRANS",
        },
        "system:pi_historian": {
            "canonical_name": "OSIsoft PI Historian",
            "display_name": "PI Historian Replica",
            "node_type": "system",
            "organization": "APEX_TRANS",
        },
    }

    forward_adjacency = {
        "person:mandy_hopmann": [
            {"to_node_id": "project:nexus", "predicate": "OWNS_OR_LEADS", "to_node_name": "Project Nexus"},
            {"to_node_id": "org:apex_trans", "predicate": "AFFILIATED_WITH", "to_node_name": "Apex Energy Transmission"},
        ],
        "project:nexus": [
            {"to_node_id": "workstream:scada_integration", "predicate": "CONTAINS", "to_node_name": "SCADA Integration Workstream"},
            {"to_node_id": "system:lone_star_ems", "predicate": "DEPENDS_ON", "to_node_name": "Lone Star EMS"},
        ],
        "workstream:scada_integration": [
            {"to_node_id": "system:pi_historian", "predicate": "USES_PATTERN", "to_node_name": "OSIsoft PI Historian"},
        ],
        "system:lone_star_ems": [
            {"to_node_id": "system:pi_historian", "predicate": "CONSTRAINS", "to_node_name": "OSIsoft PI Historian"},
        ],
        "system:pi_historian": [],
        "org:apex_trans": [],
    }

    assertions = {
        "ast-1": {
            "subject_node_id": "project:nexus",
            "predicate": "DECISION",
            "claim_text": "Proceed with Lone Star EMS pilot deployment in Q4",
            "canonical_status": "ACTIVE",
        },
        "ast-2": {
            "subject_node_id": "system:lone_star_ems",
            "predicate": "HAS_RISK",
            "claim_text": "CIP-005 airgap perimeter constraint prevents direct cloud streaming",
            "canonical_status": "ACTIVE",
        },
    }

    return SimpleNamespace(
        nodes=nodes,
        forward_adjacency=forward_adjacency,
        assertions=assertions,
    )


class TestCopilotNeighborhoodE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _build_e2e_enterprise_graph()
        self.retriever = CopilotGraphRetriever(
            topology=self.graph,
            attendees=[{"name": "harper Hoffman", "role": "VP Operations", "org": "APEX_TRANS"}],
            graph_sha256="test-sha-w3-e2e",
            generation="generation-w3-neighborhood-e2e",
        )
        self.bridge = CopilotPrefetchBridge(
            in_memory_graph=self.graph,
            retriever=self.retriever,
            timeout_seconds=5.0,
        )

    def test_prefetch_populates_connected_chains_and_enriches_fast_retrieval(self) -> None:
        """E2E lifecycle: prefetch -> packet -> retriever fast-tier enrichment -> advisor prompt."""
        packet = self.bridge.prefetch(
            project_name="project_nexus",
            meeting_name="Nexus Architecture Steering",
            attendees=[{"name": "harper Hoffman", "role": "VP Operations", "org": "APEX_TRANS"}],
        )

        # 1. Verify packet structure and multi-hop connected chains
        self.assertIsInstance(packet, SessionContextPacket)
        self.assertFalse(packet.is_empty)
        self.assertEqual(packet.prefetch_status, "SUCCESS")
        self.assertGreater(len(packet.connected_chains), 0)

        # Find 2-hop chain Mandy -> Nexus -> Lone Star EMS
        mandy_chains = [
            c for c in packet.connected_chains
            if c["seed_id"] == "person:mandy_hopmann" and c["hops"] == 2
        ]
        self.assertGreater(len(mandy_chains), 0)
        scada_chain = next((c for c in mandy_chains if c["target_id"] == "system:lone_star_ems"), None)
        self.assertIsNotNone(scada_chain)
        self.assertIn("harper Hoffman", scada_chain["chain_str"])
        self.assertIn("Project Nexus", scada_chain["chain_str"])
        self.assertIn("Lone Star EMS", scada_chain["chain_str"])

        # 2. Verify SessionContextPacket prompt formatting
        formatted_prompt = packet.format_for_copilot_prompt()
        self.assertIn("[CONNECTED]", formatted_prompt)
        self.assertIn("Project Nexus", formatted_prompt)

        # 3. Verify CopilotGraphRetriever fast-tier lookup incorporates connected chains in < 1 ms
        t0 = time.perf_counter()
        ctx = self.retriever.retrieve_context("Mandy discussed Lone Star EMS integration")
        latency_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(latency_ms, 1.0, f"retrieve_context latency {latency_ms:.3f} ms exceeds 1.0 ms budget")
        self.assertIn("Connected:", ctx)
        self.assertIn("harper Hoffman", ctx)

        # 4. Verify LiveCopilotAdvisor integrates context model and precomputed neighborhood
        model = ConversationContextModel(project_scope="project_nexus")
        model.add_turn(
            speaker="harper Hoffman",
            text="We need to finalize the Lone Star EMS architecture today.",
            timestamp="00:01:00",
            turn_sec=60.0,
        )

        advisor = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Nexus Architecture Steering",
            mock_mode=True,
            model_name="mock",
            session_context_packet=packet,
            enable_prefetch=False,
        )
        advisor.retriever = self.retriever
        advisor.context_model = model

        fused_ctx = advisor._lookup_graph_context("Mandy discussed Lone Star EMS")

        self.assertIn("[Pre-Fetched Graph Context", fused_ctx)
        self.assertIn("[CONNECTED]", fused_ctx)
        self.assertIn("Lone Star EMS", fused_ctx)


if __name__ == "__main__":
    unittest.main()
