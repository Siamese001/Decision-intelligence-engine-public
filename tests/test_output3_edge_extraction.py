#!/usr/bin/env python3
"""Unit tests for Wave 4: Output 03 Edge Traits & Predicate Margin Gating.

Verifies:
1. Multi-candidate relationship predicates evaluate margin against 15% threshold.
2. Predicates with delta < 15% trigger HitlRequirementEnvelope and update projection.
3. Predicates with delta >= 15% resolve autonomously under audit receipt.
4. Entity types with delta < 15% trigger HitlRequirementEnvelope and ratify canonical type.
5. Processed edge traits and intent profiles are correctly bound in meeting_entities.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.normalization_core.meeting_entities import process_projection_signals
from dual_pipeline.transcript_stages_core.edge_trait_evaluator import (
    evaluate_edge_traits_and_disambiguate,
)


@pytest.mark.unit
class Output3EdgeExtractionTests(unittest.TestCase):
    """Test suite verifying Wave 4 Output 03 Edge Traits and HITL margin gating."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_predicate_margin_gate_triggers_hitl_when_delta_under_15_percent(self) -> None:
        """When predicate candidate margin is < 15%, HitlRequirementEnvelope prompts operator."""
        projection = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:nexus", "name": "Nexus", "type": "system"},
                    {"id": "system:sap", "name": "SAP ERP", "type": "system"},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "rel-nexus-sap-01",
                        "from_id": "system:nexus",
                        "to_id": "system:sap",
                        "relationship": "INTEGRATES_WITH",
                        "candidate_predicates": [
                            {"predicate": "INTEGRATES_WITH", "confidence": 0.54},
                            {"predicate": "DEPENDS_ON", "confidence": 0.46},
                        ],
                    }
                ],
            }
        }
        metadata = {
            "hitl_edge_resolver": lambda env: "DEPENDS_ON",
        }

        updated_proj, updated_meta = evaluate_edge_traits_and_disambiguate(
            projection,
            metadata,
            store=self.store,
            interactive_resolver=metadata["hitl_edge_resolver"],
        )

        rel = updated_proj["graph_mutations"]["relationship_signals"][0]
        self.assertEqual(rel["relationship"], "DEPENDS_ON")
        self.assertEqual(rel["predicate"], "DEPENDS_ON")
        self.assertIn("edge_pred_system_nexus_system_sap", updated_meta["hitl_resolutions"])

        # Check ledger persistence
        records = self.store.get_history(category="edge_trait_predicate_disambiguation")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["selected_option"]["key"], "DEPENDS_ON")

    def test_predicate_margin_gate_auto_resolves_when_delta_meets_15_percent(self) -> None:
        """When predicate candidate margin >= 15%, resolves autonomously without prompting."""
        projection = {
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "rel-nexus-sap-02",
                        "from_id": "system:nexus",
                        "to_id": "system:sap",
                        "relationship": "INTEGRATES_WITH",
                        "candidate_predicates": [
                            {"predicate": "INTEGRATES_WITH", "confidence": 0.75},
                            {"predicate": "DEPENDS_ON", "confidence": 0.25},
                        ],
                    }
                ]
            }
        }
        metadata: dict = {}

        updated_proj, updated_meta = evaluate_edge_traits_and_disambiguate(
            projection,
            metadata,
            store=self.store,
        )

        rel = updated_proj["graph_mutations"]["relationship_signals"][0]
        self.assertEqual(rel["relationship"], "INTEGRATES_WITH")
        # No HITL resolution required
        self.assertNotIn("hitl_resolutions", updated_meta)

    def test_entity_type_margin_gate_disambiguation(self) -> None:
        """Ambiguous entity typing (<15% margin) triggers HITL envelope and updates entity id/type."""
        projection = {
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "concept:voc",
                        "name": "Voice of Customer",
                        "type": "concept",
                        "candidate_types": [
                            {"type": "role", "confidence": 0.52},
                            {"type": "system", "confidence": 0.48},
                        ],
                    }
                ],
                "relationship_signals": [],
            }
        }
        metadata = {
            "hitl_edge_resolver": lambda env: "role",
        }

        updated_proj, updated_meta = evaluate_edge_traits_and_disambiguate(
            projection,
            metadata,
            store=self.store,
            interactive_resolver=metadata["hitl_edge_resolver"],
        )

        ent = updated_proj["graph_mutations"]["entity_upserts"][0]
        self.assertEqual(ent["type"], "role")
        self.assertEqual(ent["id"], "role:voc")
        self.assertIn("ent_type_voice_of_customer", updated_meta["hitl_resolutions"])

    def test_process_projection_signals_binds_edge_traits(self) -> None:
        """process_projection_signals correctly populates candidates and edge_traits."""
        projection = {
            "entity_upserts": [
                {"id": "system:nexus", "name": "Nexus", "type": "system"},
                {"id": "system:sap", "name": "SAP ERP", "type": "system"},
            ],
            "relationship_signals": [
                {
                    "signal_id": "rel-01",
                    "from_id": "system:nexus",
                    "to_id": "system:sap",
                    "relationship": "INTEGRATES_WITH",
                    "candidate_predicates": [
                        {"predicate": "INTEGRATES_WITH", "confidence": 0.60},
                        {"predicate": "DEPENDS_ON", "confidence": 0.40},
                    ],
                }
            ],
        }
        candidates: list = []
        evidence = [{"evidence_id": "ev-01", "locator": {"kind": "line", "value": "10"}}]

        intent_profiles, edge_traits = process_projection_signals(
            projection=projection,
            candidates=candidates,
            evidence=evidence,
            evidence_by_identifier={},
            run_id="run_001",
            meeting_title="Nexus Sync",
            classification="INTERNAL",
            default_audiences=["team"],
        )

        self.assertGreater(len(candidates), 0)
        self.assertGreater(len(edge_traits), 0)
        first_trait = list(edge_traits.values())[0]
        self.assertEqual(first_trait["relationship_predicate"], "INTEGRATES_WITH")
        self.assertIn("candidate_predicates", first_trait)


if __name__ == "__main__":
    unittest.main()
