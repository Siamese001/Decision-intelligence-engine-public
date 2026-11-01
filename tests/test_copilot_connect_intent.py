"""
tests/test_copilot_connect_intent.py

Unit test suite for Wave 5: Copilot `connect` intent and contradiction-aware `gaps`.
Verifies multi-hop cross-SOW chain retrieval, flow-state alignment, salience ranking,
and semantic contradiction detection (conflicting vs agreeing dated assertions).
"""
import unittest
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.copilot_connect import (
    GapsResult,
    get_connected_chains,
)
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.copilot_neighborhood import (
    CopilotNeighborhoodStore,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    CopilotGraphRetriever,
)


class TestCopilotConnectIntent(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_topology = MagicMock()
        self.mock_topology.graph_sha256 = "sha256_mock_connect_intent"
        self.mock_topology.generation = 5

        self.mock_topology.nodes = {
            "workstream_grid": {
                "canonical_name": "Grid Modernization Workstream",
                "display_name": "Grid Mod",
                "node_type": "workstream",
                "org": "Apex Global",
            },
            "sow_ems_phase2": {
                "canonical_name": "SOW EMS Phase 2",
                "display_name": "EMS Phase 2",
                "node_type": "sow",
                "org": "Apex Global",
            },
            "deliverable_scada": {
                "canonical_name": "SCADA Gateway Protocol",
                "display_name": "SCADA Gateway",
                "node_type": "deliverable",
                "org": "Apex Global",
            },
            "risk_scada_latency": {
                "canonical_name": "SCADA Latency > 500ms",
                "display_name": "SCADA Latency",
                "node_type": "risk",
                "org": "Apex Global",
            },
            "person_mandy": {
                "canonical_name": "harper Hoffman",
                "display_name": "Mandy",
                "node_type": "person",
                "org": "PartnerAdvisory",
            },
        }

        self.mock_topology.assertions = {
            "ast_ws_sow": {
                "assertion_id": "ast_ws_sow",
                "subject_node_id": "workstream_grid",
                "predicate": "CONTAINS",
                "object_node_id": "sow_ems_phase2",
                "object_literal": "SOW EMS Phase 2",
                "claim_text": "Grid Modernization workstream contains SOW EMS Phase 2.",
                "observed_at": "2026-10-01",
                "source_date": "2026-10-01",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_1"],
                "source_ids": ["src_1"],
            },
            "ast_sow_del": {
                "assertion_id": "ast_sow_del",
                "subject_node_id": "sow_ems_phase2",
                "predicate": "DELIVERS",
                "object_node_id": "deliverable_scada",
                "object_literal": "SCADA Gateway Protocol",
                "claim_text": "SOW EMS Phase 2 delivers SCADA Gateway Protocol.",
                "observed_at": "2026-10-01",
                "source_date": "2026-10-01",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_2"],
                "source_ids": ["src_1"],
            },
            "ast_del_risk": {
                "assertion_id": "ast_del_risk",
                "subject_node_id": "deliverable_scada",
                "predicate": "HAS_RISK",
                "object_node_id": "risk_scada_latency",
                "object_literal": "SCADA Latency > 500ms",
                "claim_text": "SCADA Gateway Protocol has latency risk exceeding 500ms.",
                "observed_at": "2026-10-02",
                "source_date": "2026-10-02",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_3"],
                "source_ids": ["src_2"],
            },
            "ast_del_lead": {
                "assertion_id": "ast_del_lead",
                "subject_node_id": "deliverable_scada",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "person_mandy",
                "object_literal": "harper Hoffman",
                "claim_text": "harper Hoffman leads delivery for SCADA Gateway Protocol.",
                "observed_at": "2026-10-01",
                "source_date": "2026-10-01",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_4"],
                "source_ids": ["src_1"],
            },
            # Opposing dated assertion for contradiction gap testing
            "ast_del_lead_conflict": {
                "assertion_id": "ast_del_lead_conflict",
                "subject_node_id": "deliverable_scada",
                "predicate": "OWNS_OR_LEADS",
                "object_node_id": "person_external_vendor",
                "object_literal": "External Vendor Alpha",
                "claim_text": "SCADA Gateway Protocol ownership assigned to External Vendor Alpha.",
                "observed_at": "2026-10-05",
                "source_date": "2026-10-05",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_5"],
                "source_ids": ["src_3"],
            },
            # Agreeing dated assertion for agreement gap testing
            "ast_del_risk_agree": {
                "assertion_id": "ast_del_risk_agree",
                "subject_node_id": "deliverable_scada",
                "predicate": "HAS_RISK",
                "object_node_id": "risk_scada_latency",
                "object_literal": "SCADA Latency > 500ms",
                "claim_text": "SCADA Gateway Protocol has latency risk exceeding 500ms confirmed in Q4.",
                "observed_at": "2026-10-05",
                "source_date": "2026-10-05",
                "is_active": True,
                "status": "ACCEPTED",
                "evidence_ids": ["ev_6"],
                "source_ids": ["src_3"],
            },
        }

        chains = [
            {
                "seed_id": "workstream_grid",
                "target_id": "deliverable_scada",
                "hops": 2,
                "path": ["workstream_grid", "sow_ems_phase2", "deliverable_scada"],
                "predicates": ["CONTAINS", "DELIVERS"],
                "chain_str": "workstream_grid -[CONTAINS]-> sow_ems_phase2 -[DELIVERS]-> deliverable_scada",
                "is_priority": True,
                "last_claim": "SOW EMS Phase 2 delivers SCADA Gateway Protocol.",
            },
            {
                "seed_id": "deliverable_scada",
                "target_id": "risk_scada_latency",
                "hops": 1,
                "path": ["deliverable_scada", "risk_scada_latency"],
                "predicates": ["HAS_RISK"],
                "chain_str": "deliverable_scada -[HAS_RISK]-> risk_scada_latency",
                "is_priority": True,
                "last_claim": "SCADA Gateway Protocol has latency risk exceeding 500ms.",
            },
            {
                "seed_id": "deliverable_scada",
                "target_id": "person_mandy",
                "hops": 1,
                "path": ["deliverable_scada", "person_mandy"],
                "predicates": ["OWNS_OR_LEADS"],
                "chain_str": "deliverable_scada -[OWNS_OR_LEADS]-> person_mandy",
                "is_priority": True,
                "last_claim": "harper Hoffman leads delivery for SCADA Gateway Protocol.",
            },
        ]
        self.neighborhood_store = CopilotNeighborhoodStore(chains=chains, status="SUCCESS")

        self.retriever = CopilotGraphRetriever(
            topology=self.mock_topology,
            attendees=[{"name": "harper Hoffman", "role": "Lead", "org": "PartnerAdvisory"}],
            graph_sha256="sha256_mock_connect_intent",
            generation=5,
            neighborhood_store=self.neighborhood_store,
        )

    def test_get_connected_chains_surfaces_cross_sow_chains(self) -> None:
        """Verifies multi-hop cross-SOW chains are retrieved with underlying assertions."""
        context_model = ConversationContextModel(
            project_scope="workstream_grid",
            attendees=[{"name": "harper Hoffman", "role": "Lead", "org": "PartnerAdvisory"}],
        )
        context_model.add_turn(
            speaker="harper Hoffman",
            text="Let us discuss the SCADA Gateway deliverables and workstream progress.",
            timestamp="00:01:00",
            turn_sec=60.0,
        )

        chains, assertions = self.retriever.get_connected_chains(context_model, limit=3)
        self.assertGreaterEqual(len(chains), 1)
        self.assertGreaterEqual(len(assertions), 1)

        # Check chain structure
        chain_strings = [c["chain_str"] for c in chains]
        self.assertTrue(any("deliverable_scada" in cs for cs in chain_strings))

        # Check underlying assertions have IDs and dates
        for a in assertions:
            self.assertIn("assertion_id", a)
            self.assertTrue(a.get("source_date") or a.get("observed_at"))

    def test_get_connected_chains_flow_state_alignment(self) -> None:
        """Verifies flow state (objection/risk vs action) appropriately boosts relevant chains."""
        # 1. Objection / Risk flow state
        risk_model = ConversationContextModel(
            project_scope="workstream_grid",
            attendees=[{"name": "harper Hoffman", "role": "Lead", "org": "PartnerAdvisory"}],
        )
        risk_model.add_turn(
            speaker="harper Hoffman",
            text="I see a severe risk and major concern regarding SCADA performance.",
            timestamp="00:01:00",
            turn_sec=60.0,
        )
        self.assertEqual(risk_model.current_act, "objection")

        chains_risk, _ = get_connected_chains(self.retriever, risk_model, limit=2)
        self.assertGreaterEqual(len(chains_risk), 1)
        top_preds = chains_risk[0].get("predicates", [])
        self.assertIn("HAS_RISK", top_preds)

    def test_contradiction_aware_gaps_conflict_vs_agree(self) -> None:
        """
        Acceptance criterion 2:
        Two conflicting dated assertions yield a conflict item.
        Two agreeing assertions do not.
        """
        query = "SCADA Gateway Protocol ownership and latency risk"
        result = self.retriever.get_gaps(query)

        # 1. Unpacks cleanly as a 2-tuple for backward compatibility
        assertions, has_prior_record = result
        self.assertTrue(has_prior_record)
        self.assertGreaterEqual(len(assertions), 1)

        # 2. Conflicting dated assertions on (deliverable_scada, OWNS_OR_LEADS) yield a conflict item
        self.assertIsInstance(result, GapsResult)
        conflicts = result.conflicts
        self.assertGreaterEqual(len(conflicts), 1)

        conflict_subjects = [c["subject"] for c in conflicts]
        self.assertIn("deliverable_scada", conflict_subjects)
        conflict_preds = [c["predicate"] for c in conflicts]
        self.assertIn("OWNS_OR_LEADS", conflict_preds)

        # Verify the conflict details state the opposing values
        lead_conflict = next(c for c in conflicts if c["predicate"] == "OWNS_OR_LEADS")
        self.assertIn("harper Hoffman", lead_conflict["claim_a"])
        self.assertIn("External Vendor Alpha", lead_conflict["claim_b"])

        # 3. Two agreeing dated assertions on (deliverable_scada, HAS_RISK) do NOT yield a conflict
        risk_conflicts = [c for c in conflicts if c["predicate"] == "HAS_RISK"]
        self.assertEqual(len(risk_conflicts), 0, "Agreeing assertions must not yield a conflict item")

    def test_get_connected_chains_zero_chains_fails_closed(self) -> None:
        """Verifies that with 0 grounded chains, returns empty lists (fail-closed)."""
        retriever_empty = CopilotGraphRetriever(
            topology=self.mock_topology,
            attendees=[],
            neighborhood_store=CopilotNeighborhoodStore(chains=[], status="EMPTY"),
        )
        chains, assertions = retriever_empty.get_connected_chains("completely unrelated noise query")
        self.assertEqual(chains, [])
        self.assertEqual(assertions, [])


if __name__ == "__main__":
    unittest.main()
