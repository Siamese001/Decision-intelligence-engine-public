"""End-to-end tests for Edge QRELS compilation and retrieval release gate floor."""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.core import read_json, sha256_file
from dual_pipeline.retrieval_eval_core.edge_gate import audit_edge_recall_and_leaks
from tools.generate_edge_qrels import build_edge_qrels_dataset

ROOT = Path(__file__).resolve().parents[2]


class TestEdgeQrelsReleaseGateE2E(unittest.TestCase):
    def test_edge_qrels_generation_and_gate_floor_e2e(self) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.is_dir():
            self.skipTest(".local/dual_pipeline directory not present")

        # Run generator
        dataset = build_edge_qrels_dataset(runtime_root, show_progress=False)

        # 1. Artifact structure & RFC 8785 canonical digest validation
        self.assertEqual(dataset["schema_version"], "1.0.0")
        self.assertIn("dataset_id", dataset)
        self.assertTrue(dataset["dataset_id"].startswith("edge-qrels-"))
        self.assertIn("canonical_digest", dataset)
        self.assertEqual(dataset["canonical_digest"]["algorithm"], "sha256")
        self.assertEqual(len(dataset["canonical_digest"]["value"]), 64)

        # 2. Gate floor acceptance criteria
        gate_val = dataset["gate_validation"]
        self.assertEqual(gate_val["status"], "PASS")
        self.assertTrue(gate_val["passed"])
        self.assertEqual(gate_val["failures"], [])

        # Acceptance Criteria Check: >=60 name-free paraphrases
        name_free_cases = dataset["name_free_cases"]
        self.assertGreaterEqual(
            len(name_free_cases), 60,
            f"Expected >=60 name-free paraphrases, got {len(name_free_cases)}",
        )

        # Acceptance Criteria Check: >=360 entity/claim control set
        control_cases = dataset["control_cases"]
        self.assertGreaterEqual(
            len(control_cases), 360,
            f"Expected >=360 control cases, got {len(control_cases)}",
        )

        # Acceptance Criteria Check: Ported 21 competency cases (RB-001 to RB-021)
        competency_cases = dataset["competency_cases"]
        self.assertEqual(len(competency_cases), 21)
        competency_ids = [c["case_id"] for c in competency_cases]
        for i in range(1, 22):
            self.assertIn(f"RB-{i:03d}", competency_ids)

        # 3. Predicate diversity floor
        stats = dataset["statistics"]
        self.assertGreaterEqual(stats["predicates_covered_count"], 10)
        self.assertGreaterEqual(len(stats["predicates_covered"]), 10)

        # 4. Zero mutation verification
        active_pair = read_json(runtime_root / "active_pair.json")
        graph_path = runtime_root / active_pair["graph_path"]
        sidecar_path = runtime_root / active_pair["sidecar_path"]

        current_graph_sha = sha256_file(graph_path)
        current_sidecar_sha = sha256_file(sidecar_path)
        self.assertEqual(current_graph_sha, dataset["source_hashes"]["graph_sha256"])
        self.assertEqual(current_sidecar_sha, dataset["source_hashes"]["sidecar_sha256"])

        # 5. End-to-end audit simulation with mock retrieval
        mock_results = {}
        for case in dataset["edge_cases"]:
            cid = case["case_id"]
            exp = case.get("expected_record_ids", [])
            mock_results[cid] = list(exp) + ["extra-rec-1", "extra-rec-2"]

        for case in name_free_cases:
            cid = case["case_id"]
            exp = case.get("expected_record_ids", [])
            mock_results[cid] = list(exp)

        for case in control_cases:
            cid = case["case_id"]
            exp = case.get("expected_record_ids", [])
            mock_results[cid] = list(exp)

        audit_res = audit_edge_recall_and_leaks(mock_results, dataset)
        self.assertEqual(audit_res["status"], "PASS")
        self.assertEqual(audit_res["leak_count"], 0)
        self.assertEqual(audit_res["overall_metrics"]["recall_at_1"], 1.0)
        self.assertEqual(audit_res["crowding_analysis"]["measured_entity_s1"], 1.0)
        self.assertFalse(audit_res["crowding_analysis"]["crowding_risk_elevated"])


if __name__ == "__main__":
    unittest.main()
