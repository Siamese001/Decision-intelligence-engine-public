"""Unit tests for semantic unit policy, schema validation, and ledger alignment."""
from __future__ import annotations

import json
import unittest

from dual_pipeline.core import ROOT
from dual_pipeline.index_core.policy import (
    DEFAULT_POLICY_PATH,
    DEFAULT_SCHEMA_PATH,
    classify_policy_proposition_eligibility,
    load_semantic_unit_policy,
    project_policy_reachability,
    verify_policy_ledger_alignment,
)


class TestSemanticUnitPolicy(unittest.TestCase):
    def test_01_policy_file_exists_and_loads_cleanly(self):
        self.assertTrue(DEFAULT_POLICY_PATH.is_file(), f"Policy missing at {DEFAULT_POLICY_PATH}")
        self.assertTrue(DEFAULT_SCHEMA_PATH.is_file(), f"Schema missing at {DEFAULT_SCHEMA_PATH}")
        policy = load_semantic_unit_policy(DEFAULT_POLICY_PATH)
        self.assertEqual(policy["contract_id"], "urn:nee:dual-pipeline:semantic-unit-policy:1.0.0")
        self.assertEqual(policy["schema_version"], "1.0.0")
        self.assertEqual(policy["status"], "approved")

    def test_02_policy_ledger_alignment(self):
        policy = load_semantic_unit_policy(DEFAULT_POLICY_PATH)
        aligned, err = verify_policy_ledger_alignment(policy)
        self.assertTrue(aligned, f"Ledger alignment failed: {err}")
        self.assertIsNone(err)

        decisions = policy["ledger_reference"]["decisions"]
        self.assertEqual(len(decisions), 6)
        expected_ids = {
            "op-semantic-unit-w2-01-passage-windowing",
            "op-semantic-unit-w2-02-proposition-eligibility",
            "op-semantic-unit-w2-03-decontextualization-template",
            "op-semantic-unit-w2-04-candidate-lane-passages",
            "op-semantic-unit-w2-05-profile-hop-budget",
            "op-semantic-unit-w2-06-passage-security-inheritance",
        }
        self.assertEqual({d["decision_id"] for d in decisions}, expected_ids)

    def test_03_proposition_eligibility_classification(self):
        policy = load_semantic_unit_policy(DEFAULT_POLICY_PATH)

        # 1. Valid proposition with literal claim
        prop_row = {
            "assertion_id": "asrt_prop_1",
            "assertion_type": "decision",
            "object_literal": "We decided to deploy the service on Friday.",
            "object_node_id": None,
        }
        eligible, reason = classify_policy_proposition_eligibility(prop_row, policy)
        self.assertTrue(eligible)
        self.assertEqual(reason, "ELIGIBLE_PROPOSITION")

        # 2. Structural edge (has object_node_id, no literal)
        edge_row = {
            "assertion_id": "asrt_edge_1",
            "assertion_type": "decision",
            "object_literal": None,
            "object_node_id": "node_project_nexus",
        }
        eligible, reason = classify_policy_proposition_eligibility(edge_row, policy)
        self.assertFalse(eligible)
        self.assertEqual(reason, "EXCLUDED_STRUCTURAL_EDGE_TRAVERSAL")

        # 3. Missing literal claim
        empty_row = {
            "assertion_id": "asrt_empty_1",
            "assertion_type": "decision",
            "object_literal": "",
            "object_node_id": None,
        }
        eligible, reason = classify_policy_proposition_eligibility(empty_row, policy)
        self.assertFalse(eligible)
        self.assertEqual(reason, "EXCLUDED_MISSING_LITERAL_CLAIM")

        # 4. Ineligible assertion type
        ineligible_type_row = {
            "assertion_id": "asrt_type_1",
            "assertion_type": "unrecognized_custom_type",
            "object_literal": "A valid claim with invalid type.",
            "object_node_id": None,
        }
        eligible, reason = classify_policy_proposition_eligibility(ineligible_type_row, policy)
        self.assertFalse(eligible)
        self.assertIn("EXCLUDED_ASSERTION_TYPE", reason)

    def test_04_policy_reachability_projection_zero_gap(self):
        policy = load_semantic_unit_policy(DEFAULT_POLICY_PATH)
        rt_root = ROOT / ".local" / "dual_pipeline"
        pair_file = rt_root / "active_pair.json"
        if not pair_file.is_file():
            self.skipTest("No active_pair.json found in runtime root")

        with open(pair_file, encoding="utf-8") as f:
            pair = json.load(f)
        graph_path = rt_root / pair["graph_path"]
        sidecar_path = rt_root / pair["sidecar_path"]

        projected = project_policy_reachability(
            graph_path=graph_path,
            sidecar_path=sidecar_path,
            policy=policy,
            hop_budget=1,
        )

        self.assertEqual(projected["projected_unreachable_unexplained_count"], 0)
        self.assertEqual(projected["projected_unreachable_unexplained_ids"], [])
        self.assertGreater(projected["projected_direct_propositions"], 0)
        self.assertGreater(projected["projected_dense_edges_removed"], 0)
        self.assertGreater(projected["projected_reachable_via_passage"], 0)
        self.assertEqual(
            projected["projected_total_reachable"] + projected["projected_excluded_policy_count"],
            projected["total_current_assertions"],
        )


if __name__ == "__main__":
    unittest.main()
