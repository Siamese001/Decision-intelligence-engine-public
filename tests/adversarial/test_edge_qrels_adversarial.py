"""Adversarial and failure-mode tests for Edge QRELS and retrieval evaluation gate.

Covers:
1. Sub-floor rejection across all 4 mandatory floor thresholds.
2. Leak detection (historical assertions, needs_review leakage, forbidden IDs).
3. Failed safe abstention detection.
4. Entity facet crowding degradation detection.
5. Malformed suite structures and boundary cases.
"""
from __future__ import annotations

import unittest

from dual_pipeline.retrieval_eval_core.edge_gate import (
    audit_edge_recall_and_leaks,
    validate_edge_gate_floors,
)


class TestEdgeQrelsAdversarial(unittest.TestCase):
    def test_adversarial_sub_floor_rejection_approved(self) -> None:
        suite = {
            "edge_cases": [
                {"case_id": f"APP-{i}", "lane": "approved", "predicate": f"PRED_{i % 12}", "expected_record_ids": [f"r{i}"]}
                for i in range(24)
            ] + [
                {"case_id": f"CAN-{i}", "lane": "candidate", "predicate": f"PRED_{i % 12}", "expected_record_ids": [f"rc{i}"]}
                for i in range(12)
            ],
            "name_free_cases": [{"case_id": f"NF-{i}", "lane": "candidate", "expected_record_ids": [f"rn{i}"]} for i in range(16)],
        }
        result = validate_edge_gate_floors(suite)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["passed"])
        self.assertTrue(any("Approved edge gold count" in f for f in result["failures"]))

    def test_adversarial_sub_floor_rejection_candidate(self) -> None:
        suite = {
            "edge_cases": [
                {"case_id": f"APP-{i}", "lane": "approved", "predicate": f"PRED_{i % 12}", "expected_record_ids": [f"r{i}"]}
                for i in range(26)
            ] + [
                {"case_id": f"CAN-{i}", "lane": "candidate", "predicate": f"PRED_{i % 12}", "expected_record_ids": [f"rc{i}"]}
                for i in range(9)
            ],
            "name_free_cases": [{"case_id": f"NF-{i}", "expected_record_ids": [f"rn{i}"]} for i in range(16)],
        }
        result = validate_edge_gate_floors(suite)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["passed"])
        self.assertTrue(any("Candidate edge gold count" in f for f in result["failures"]))

    def test_adversarial_sub_floor_rejection_predicates(self) -> None:
        suite = {
            "edge_cases": [
                {"case_id": f"APP-{i}", "lane": "approved", "predicate": f"PRED_{i % 5}", "expected_record_ids": [f"r{i}"]}
                for i in range(26)
            ] + [
                {"case_id": f"CAN-{i}", "lane": "candidate", "predicate": f"PRED_{i % 5}", "expected_record_ids": [f"rc{i}"]}
                for i in range(12)
            ],
            "name_free_cases": [{"case_id": f"NF-{i}", "expected_record_ids": [f"rn{i}"]} for i in range(16)],
        }
        result = validate_edge_gate_floors(suite)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["passed"])
        self.assertTrue(any("Distinct predicate count" in f for f in result["failures"]))

    def test_adversarial_sub_floor_rejection_name_free(self) -> None:
        suite = {
            "edge_cases": [
                {"case_id": f"APP-{i}", "lane": "approved", "predicate": f"PRED_{i % 12}", "expected_record_ids": [f"r{i}"]}
                for i in range(26)
            ] + [
                {"case_id": f"CAN-{i}", "lane": "candidate", "predicate": f"PRED_{i % 12}", "expected_record_ids": [f"rc{i}"]}
                for i in range(12)
            ],
            "name_free_cases": [{"case_id": f"NF-{i}", "expected_record_ids": [f"rn{i}"]} for i in range(10)],
        }
        result = validate_edge_gate_floors(suite)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["passed"])
        self.assertTrue(any("Name-free paraphrase count" in f for f in result["failures"]))

    def test_adversarial_leak_detection_forbidden_record(self) -> None:
        dataset = {
            "edge_cases": [
                {
                    "case_id": "EQ-001",
                    "lane": "approved",
                    "predicate": "ROUTES_TO",
                    "expected_record_ids": ["rec-approved-1"],
                    "forbidden_record_ids": ["rec-historical-leak"],
                }
            ],
            "adversarial_cases": [
                {
                    "case_id": "ADV-001",
                    "adversarial_category": "historical_leak",
                    "expected_abstention": True,
                    "forbidden_record_ids": ["rec-historical-leak"],
                }
            ],
        }

        polluted_results = {
            "EQ-001": ["rec-historical-leak", "rec-approved-1"],
            "ADV-001": [],
        }

        audit = audit_edge_recall_and_leaks(polluted_results, dataset)
        self.assertEqual(audit["status"], "LEAKS_DETECTED")
        self.assertGreaterEqual(audit["leak_count"], 1)
        leaked_ids = [lk["leaked_record_ids"] for lk in audit["leaks"]]
        self.assertTrue(any("rec-historical-leak" in ids for ids in leaked_ids))

    def test_adversarial_failed_abstention_detection(self) -> None:
        dataset = {
            "edge_cases": [],
            "adversarial_cases": [
                {
                    "case_id": "ADV-ABSTAIN",
                    "adversarial_category": "ambiguous_identity",
                    "expected_abstention": True,
                    "expected_record_ids": [],
                }
            ],
        }

        violating_results = {
            "ADV-ABSTAIN": ["guessed-record-id"],
        }

        audit = audit_edge_recall_and_leaks(violating_results, dataset)
        self.assertEqual(audit["status"], "LEAKS_DETECTED")
        self.assertGreaterEqual(audit["leak_count"], 1)
        self.assertTrue(any(lk["leak_category"] == "failed_abstention" for lk in audit["leaks"]))

    def test_adversarial_crowding_degradation_flag(self) -> None:
        dataset = {
            "edge_cases": [],
            "control_cases": [
                {
                    "case_id": f"CTRL-{i}",
                    "target_kind": "entity",
                    "expected_record_ids": [f"facet-{i}"],
                }
                for i in range(50)
            ],
        }

        crowded_results = {
            f"CTRL-{i}": [f"crowding-edge-{i}", f"facet-{i}"]
            for i in range(50)
        }

        audit = audit_edge_recall_and_leaks(crowded_results, dataset)
        self.assertEqual(audit["crowding_analysis"]["measured_entity_s1"], 0.0)
        self.assertTrue(audit["crowding_analysis"]["crowding_risk_elevated"])
        self.assertGreater(audit["crowding_analysis"]["crowding_s1_drop"], 0.05)

    def test_adversarial_malformed_dataset_empty_payloads(self) -> None:
        empty_dataset = {}
        result = validate_edge_gate_floors(empty_dataset)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["failures"]), 4)

        empty_audit = audit_edge_recall_and_leaks({}, {})
        self.assertEqual(empty_audit["status"], "PASS")
        self.assertEqual(empty_audit["total_edge_cases_evaluated"], 0)
        self.assertEqual(empty_audit["leak_count"], 0)


if __name__ == "__main__":
    unittest.main()
