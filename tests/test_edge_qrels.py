"""Unit tests for Edge QRELS generation and retrieval evaluation edge gate."""
from __future__ import annotations

import unittest
from pathlib import Path

from dual_pipeline.retrieval_eval_core.edge_gate import (
    DEFAULT_EDGE_FLOORS,
    audit_edge_recall_and_leaks,
    extend_evaluation_with_edge_gate,
    validate_edge_gate_floors,
)
from retrieval_tests.edge_competency_data import (
    NAME_FREE_PARAPHRASES,
    PORTED_COMPETENCY_CASES,
    PREDICATE_PHRASES,
)

ROOT = Path(__file__).resolve().parents[1]


class TestEdgeQrelsUnit(unittest.TestCase):
    def test_predicate_phrases_and_paraphrase_coverage(self) -> None:
        self.assertGreaterEqual(len(PREDICATE_PHRASES), 15)
        self.assertGreaterEqual(len(NAME_FREE_PARAPHRASES), 60)
        self.assertEqual(len(PORTED_COMPETENCY_CASES), 21)

        for nf in NAME_FREE_PARAPHRASES:
            self.assertIn("case_id", nf)
            self.assertIn("query", nf)
            self.assertIn("predicate", nf)
            self.assertTrue(
                nf["predicate"] in PREDICATE_PHRASES
                or nf["predicate"] in ("CANDIDATE_FOR", "CONSTRAINS", "TRANSITIONS_TO", "HAS_ROLE_IN")
            )

    def test_ported_competency_cases_structure(self) -> None:
        case_ids = [c["case_id"] for c in PORTED_COMPETENCY_CASES]
        expected_ids = [f"RB-{i:03d}" for i in range(1, 22)]
        self.assertEqual(case_ids, expected_ids)

        abstention_cases = [c for c in PORTED_COMPETENCY_CASES if c.get("expected_abstention")]
        self.assertGreaterEqual(len(abstention_cases), 5)
        for ac in abstention_cases:
            self.assertEqual(ac["expected_record_ids"], [])

    def test_validate_edge_gate_floors_passes_on_valid_dataset(self) -> None:
        dummy_cases = []
        for i in range(30):
            dummy_cases.append({
                "case_id": f"C-APP-{i}",
                "lane": "approved",
                "predicate": f"PRED_{i % 12}",
                "expected_record_ids": [f"rec-app-{i}"],
            })
        for i in range(15):
            dummy_cases.append({
                "case_id": f"C-CAN-{i}",
                "lane": "candidate",
                "predicate": f"PRED_{i % 12}",
                "expected_record_ids": [f"rec-can-{i}"],
            })
        name_free_cases = [
            {"case_id": f"NF-{i}", "expected_record_ids": [f"rec-nf-{i}"]}
            for i in range(20)
        ]

        dataset = {
            "edge_cases": dummy_cases,
            "name_free_cases": name_free_cases,
        }

        result = validate_edge_gate_floors(dataset)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["passed"])
        self.assertEqual(result["failures"], [])
        self.assertGreaterEqual(result["counts"]["approved_edge_gold_count"], DEFAULT_EDGE_FLOORS["min_approved"])
        self.assertGreaterEqual(result["counts"]["candidate_edge_gold_count"], DEFAULT_EDGE_FLOORS["min_candidate"])
        self.assertGreaterEqual(result["counts"]["distinct_predicates_count"], DEFAULT_EDGE_FLOORS["min_predicates"])
        self.assertGreaterEqual(result["counts"]["name_free_count"], DEFAULT_EDGE_FLOORS["min_name_free"])

    def test_validate_edge_gate_floors_fails_on_sub_floors(self) -> None:
        dataset = {
            "edge_cases": [
                {"case_id": "C-1", "lane": "approved", "predicate": "ROUTES_TO", "expected_record_ids": ["r1"]},
            ],
            "name_free_cases": [],
        }
        result = validate_edge_gate_floors(dataset)
        self.assertEqual(result["status"], "FAIL")
        self.assertFalse(result["passed"])
        self.assertGreater(len(result["failures"]), 0)

    def test_audit_edge_recall_and_leaks_clean_run(self) -> None:
        dataset = {
            "edge_cases": [
                {
                    "case_id": "EQ-001",
                    "predicate": "ROUTES_TO",
                    "query_form": "forward",
                    "degree_bucket": "degree_1",
                    "lane": "approved",
                    "expected_record_ids": ["rec-001"],
                },
                {
                    "case_id": "EQ-002",
                    "predicate": "REPORTS_TO",
                    "query_form": "reverse",
                    "degree_bucket": "hub",
                    "lane": "approved",
                    "expected_record_ids": ["rec-002"],
                },
            ],
            "name_free_cases": [
                {
                    "case_id": "NF-001",
                    "predicate": "OWNS_OR_LEADS",
                    "query_form": "name_free_paraphrase",
                    "lane": "approved",
                    "expected_record_ids": ["rec-003"],
                }
            ],
            "control_cases": [
                {
                    "case_id": "CTRL-001",
                    "target_kind": "entity",
                    "expected_record_ids": ["facet-001"],
                }
            ],
            "adversarial_cases": [
                {
                    "case_id": "ADV-001",
                    "expected_abstention": True,
                    "forbidden_record_ids": ["rec-forbidden"],
                }
            ],
        }

        results = {
            "EQ-001": ["rec-001", "other-1"],
            "EQ-002": ["rec-002", "other-2"],
            "NF-001": ["rec-003"],
            "CTRL-001": ["facet-001"],
            "ADV-001": [],
        }

        audit = audit_edge_recall_and_leaks(results, dataset)
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["leak_count"], 0)
        self.assertEqual(audit["overall_metrics"]["recall_at_1"], 1.0)
        self.assertEqual(audit["overall_metrics"]["recall_at_5"], 1.0)
        self.assertEqual(audit["overall_metrics"]["mrr"], 1.0)
        self.assertEqual(audit["crowding_analysis"]["measured_entity_s1"], 1.0)
        self.assertFalse(audit["crowding_analysis"]["crowding_risk_elevated"])

    def test_extend_evaluation_with_edge_gate(self) -> None:
        evaluation = {
            "evaluation_id": "eval-test-123",
            "gates": {"core_thresholds": True, "real_bge_m3": True},
            "status": "PASS",
        }
        valid_qrels = {
            "edge_cases": [
                {"case_id": f"C-APP-{i}", "lane": "approved", "predicate": f"P{i%12}", "expected_record_ids": [f"r{i}"]}
                for i in range(30)
            ] + [
                {"case_id": f"C-CAN-{i}", "lane": "candidate", "predicate": f"P{i%12}", "expected_record_ids": [f"rc{i}"]}
                for i in range(15)
            ],
            "name_free_cases": [{"case_id": f"NF-{i}", "expected_record_ids": [f"rn{i}"]} for i in range(20)],
        }

        extended = extend_evaluation_with_edge_gate(evaluation, valid_qrels)
        self.assertTrue(extended["gates"]["edge_gate"])
        self.assertEqual(extended["status"], "PASS")

        invalid_qrels = {"edge_cases": [], "name_free_cases": []}
        failed = extend_evaluation_with_edge_gate(evaluation, invalid_qrels)
        self.assertFalse(failed["gates"]["edge_gate"])
        self.assertEqual(failed["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
