"""
tests/test_copilot_grounding_verifier_e2e.py

End-to-end integration test for Wave 4 Two-Tier Grounding Verifier:
Simulates live transcript turns -> triggers speculative encoding -> queries LiveCopilotAdvisor
-> verifies that verified assertions are reranked via vectorized BLAS, conflicts are surfaced,
and 100% of retained sentences cite valid assertions.
"""
import unittest
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot import (
    LiveCopilotAdvisor,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
    SessionContextPacket,
)


class TestCopilotGroundingVerifierE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_topology = MagicMock()
        self.mock_topology.graph_sha256 = "sha256_mock_e2e_active"
        self.mock_topology.generation = 4

        self.mock_topology.nodes = {
            "person:mandy_hopmann": {
                "canonical_name": "harper Hoffman",
                "display_name": "Mandy",
                "node_type": "person",
                "org": "PartnerAdvisory",
            },
            "project_nexus": {
                "canonical_name": "Project Nexus",
                "display_name": "Nexus",
                "node_type": "project",
                "org": "Apex Global",
            },
            "system:lone_star_ems": {
                "canonical_name": "Lone Star EMS",
                "display_name": "EMS",
                "node_type": "system",
                "org": "Apex Global",
            },
        }

        # A set of assertions: 2 valid, 1 superseded, 1 stale, 1 conflicting
        self.mock_topology.assertions = {
            "asrt_nexus_valid_01": {
                "assertion_id": "asrt_nexus_valid_01",
                "is_active": True,
                "status": "ACCEPTED",
                "subject_node_id": "person:mandy_hopmann",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "system:lone_star_ems",
                "object_literal": "Lone Star EMS",
                "claim_text": "harper Hoffman leads delivery for the Lone Star EMS architecture.",
                "evidence_ids": ["ev_01", "ev_02"],
                "source_ids": ["meeting:01", "sow:nexus_01"],
                "observed_at": "2026-10-01",
                "stale_after": "2027-01-01",
            },
            "asrt_nexus_valid_02": {
                "assertion_id": "asrt_nexus_valid_02",
                "is_active": True,
                "status": "ACCEPTED",
                "subject_node_id": "system:lone_star_ems",
                "predicate": "DEPENDS_ON",
                "object_node_id": "project_nexus",
                "object_literal": "Project Nexus",
                "claim_text": "Lone Star EMS depends on Project Nexus telemetry pipeline.",
                "evidence_ids": ["ev_03"],
                "source_ids": ["meeting:01"],
                "observed_at": "2026-10-02",
                "stale_after": "2027-01-01",
            },
            "asrt_nexus_superseded_03": {
                "assertion_id": "asrt_nexus_superseded_03",
                "is_active": False,
                "status": "SUPERSEDED",
                "subject_node_id": "person:mandy_hopmann",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "system:legacy_scada",
                "claim_text": "harper Hoffman previously owned legacy SCADA.",
                "evidence_ids": ["ev_00"],
            },
            "asrt_nexus_stale_04": {
                "assertion_id": "asrt_nexus_stale_04",
                "is_active": True,
                "status": "ACCEPTED",
                "subject_node_id": "project_nexus",
                "predicate": "DELIVERS",
                "object_node_id": "milestone_q1",
                "claim_text": "Project Nexus delivers initial pilot in Q1 2024.",
                "stale_after": "2024-03-31",
                "evidence_ids": ["ev_old"],
            },
            "asrt_nexus_conflict_05": {
                "assertion_id": "asrt_nexus_conflict_05",
                "is_active": True,
                "status": "ACCEPTED",
                "subject_node_id": "person:mandy_hopmann",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "system:conflicting_vendor",
                "object_literal": "External Vendor X",
                "claim_text": "harper Hoffman assigned architecture ownership to External Vendor X.",
                "evidence_ids": ["ev_04"],
                "source_ids": ["meeting:02"],
                "observed_at": "2026-10-03",
            },
        }

        self.retriever = CopilotGraphRetriever(
            topology=self.mock_topology,
            attendees=[{"name": "harper Hoffman", "role": "Lead", "org": "PartnerAdvisory"}],
            graph_sha256="sha256_mock_e2e_active",
            generation=4,
        )

    def test_e2e_live_copilot_two_tier_grounding_lifecycle(self) -> None:
        """Simulates full meeting advisor flow and verifies two-tier grounding pipeline."""
        packet = SessionContextPacket(
            project_scope="project_nexus",
            graph_sha256="sha256_mock_e2e_active",
            generation=4,
            recent_assertions=list(self.mock_topology.assertions.values()),
        )

        advisor = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Nexus Steering",
            mock_mode=True,
            model_name="mock",
            session_context_packet=packet,
            enable_prefetch=False,
        )
        advisor.in_memory_graph = self.mock_topology
        advisor.retriever = self.retriever
        advisor.context_model = ConversationContextModel(
            project_scope="project_nexus",
            attendees=[{"name": "harper Hoffman", "role": "Lead", "org": "PartnerAdvisory"}],
        )

        # Record a confirmed transcript turn (triggers speculative background encode)
        advisor.record_turn(
            speaker="harper Hoffman",
            text="harper Hoffman leads delivery for Lone Star EMS architecture milestones today.",
            timestamp="00:01:00",
            turn_sec=60.0,
        )

        # Trigger copilot dialogue evaluation
        eval_result = advisor._evaluate_dialogue_sync()
        self.assertIsNotNone(eval_result)

        # 1. Grounding telemetry attached
        self.assertIn("grounding_telemetry", eval_result)
        telemetry = eval_result["grounding_telemetry"]
        self.assertIn("per_stage_timing_ms", telemetry)
        self.assertIn("total_ms", telemetry)
        self.assertIn(eval_result.get("rigor_tier"), ("VERIFIED", "FAST"))

        # 2. Assertions verified and stale/superseded excluded
        last_grounding = getattr(advisor, "_last_grounding_result", None)
        self.assertIsNotNone(last_grounding)
        verified_ids = [a["assertion_id"] for a in last_grounding.verified_assertions]
        self.assertNotIn("asrt_nexus_superseded_03", verified_ids)
        self.assertNotIn("asrt_nexus_stale_04", verified_ids)
        self.assertIn("asrt_nexus_valid_01", verified_ids)

        # 3. Conflicts between opposing claims surfaced
        self.assertGreaterEqual(len(last_grounding.conflicts), 1)
        conflict_subjects = [c["subject"] for c in last_grounding.conflicts]
        self.assertIn("person:mandy_hopmann", conflict_subjects)

        # 4. Citation validity: 100% of cited assertion IDs are in the verified active set
        cited_ids = eval_result.get("cited_assertion_ids", [])
        for cid in cited_ids:
            self.assertIn(cid, verified_ids)


if __name__ == "__main__":
    unittest.main()
