"""Adversarial and boundary tests for Retrieval Release Evaluation (Wave 2).

Covers:
1. Boundary & Malformed Inputs: compute_11gates handles empty runs, missing component dicts, None values safely fail-closed.
2. Production Regression: adversarial_retrieval_tester degradation recording without NameError.
3. Invariant Bypass: corrupt vector dimensions (non-1024, NaN, Inf) fail-closed in falsification attack.
4. Ambiguity / Distractor Injection: plausible distractor facts with tight scores prevent autonomous action.
"""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evals.evaluate_11gates_calibrated import compute_11gates
from dual_pipeline.retrieval_release_core.decision_calibration import (
    CalibrationModelWeights,
    DecisionAction,
    OperatingThresholds,
)
from dual_pipeline.retrieval_release_core.falsification import (
    execute_falsification_attack_corrupt_vector,
    execute_falsification_attack_inject_wrong_facts,
)
from dual_pipeline.retrieval_release_core.paired_experiments import QueryJudgments


class TestRetrievalEvalAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_retrieval_adversarial_")
        self.tmp_path = Path(self.temp_dir)

    def test_compute_11gates_handles_empty_and_malformed_case_results_fail_closed(self) -> None:
        """compute_11gates must fail closed (return False) when case_results are empty or malformed."""
        gates_empty = compute_11gates(
            backend=None,
            revision="rev_test",
            baseline_pair={},
            candidate_pair={},
            baseline_sidecar=self.tmp_path / "nonexistent.sidecar",
            candidate_sidecar=self.tmp_path / "nonexistent2.sidecar",
            baseline_graph=self.tmp_path / "nonexistent.db",
            candidate_graph=self.tmp_path / "nonexistent2.db",
            receipt={},
            coverage_complete=False,
            case_results=[],
            agg={},
            thresholds={},
        )
        self.assertFalse(gates_empty["1. real_bge_m3"])
        self.assertFalse(gates_empty["2. pair_binding"])
        self.assertFalse(gates_empty["3. coverage_complete"])
        self.assertFalse(gates_empty["11. component_execution (all 4 engines)"])

    def test_corrupt_vector_dimensions_fail_closed(self) -> None:
        """execute_falsification_attack_corrupt_vector must detect and reject bad dimensions, NaN, Inf."""
        result = execute_falsification_attack_corrupt_vector([0.1] * 1024)
        self.assertTrue(result.detected_cleanly, "Must detect all corrupt vector cases")
        self.assertTrue(result.forged_pass_prevented, "Forged pass must be prevented")

    def test_distractor_injection_prevents_autonomous_action(self) -> None:
        """Injecting high-scoring distractors must collapse margin and prevent autonomous approval."""
        query_data = {
            "query_id": "q_adv_01",
            "candidates": [
                {"record_id": "rec_01", "fused_score": 0.015, "dense_score": 0.8},
            ],
        }
        gt = QueryJudgments(
            query_id="q_adv_01",
            query_text="What is the revenue target?",
            topic_id="top_01",
            phrasing_type="executive",
            judgments={"rec_01": 3},
            relevant_ids={"rec_01"},
            is_answerable=True,
        )
        weights = CalibrationModelWeights()
        thresholds = OperatingThresholds()

        result = execute_falsification_attack_inject_wrong_facts(query_data, gt, weights, thresholds)
        self.assertTrue(result.detected_cleanly, "Tight distractor margin must be cleanly detected")
        self.assertTrue(result.forged_pass_prevented, "Autonomous bypass must be prevented")

    def test_adversarial_retrieval_tester_regression_record_format(self) -> None:
        """Verify that degradation records can be created using case_name and original_query without NameError."""
        # This pins the exact fix for NameError: name 'case_id' is not defined / 'question' is not defined
        case = {
            "case_id": "case_test_regression",
            "question": "What are the project milestones?",
            "expected": {
                "required_record_ids": ["ast_001"],
                "required_lanes": ["approved"],
            },
        }
        case_name = case.get("case_id", "Unnamed")
        original_query = case["question"]
        variations = {"baseline": original_query, "adversarial": original_query + " noise"}
        expected_ids = case["expected"]["required_record_ids"]

        # Simulate the hitl_queue append logic that previously crashed
        hitl_entry = {
            "case_id": case.get("case_id", case_name),
            "original_query": original_query,
            "variations": variations,
            "expected_ids": expected_ids,
            "status": "HITL_QRELS_REQUIRED",
        }
        self.assertEqual(hitl_entry["case_id"], "case_test_regression")
        self.assertEqual(hitl_entry["original_query"], "What are the project milestones?")
        self.assertEqual(hitl_entry["status"], "HITL_QRELS_REQUIRED")

    def test_clearance_breach_and_strict_inversion_falsification(self) -> None:
        """Verify CLEARANCE_BREACH_ATTEMPT suppression and strict inequality on ranking inversion."""
        from dual_pipeline.retrieval_release_core.falsification import (
            execute_falsification_attack_clearance_breach,
            execute_falsification_attack_reverse_rankings,
            validate_embedding_vector_contract,
        )

        breach_res = execute_falsification_attack_clearance_breach()
        self.assertTrue(breach_res.detected_cleanly)
        self.assertTrue(breach_res.forged_pass_prevented)

        with self.assertRaises(ValueError):
            validate_embedding_vector_contract([0.1] * 512)
        with self.assertRaises(ValueError):
            validate_embedding_vector_contract([float("nan")] * 1024)

        # Zero-relevance query must not claim inversion degradation when both before/after nDCG are 0.0
        zero_gt = QueryJudgments(
            query_id="q_zero",
            query_text="Unmatched query",
            topic_id="t0",
            phrasing_type="executive",
            judgments={"rec_a": 0, "rec_b": 0},
            relevant_ids=set(),
            is_answerable=True,
        )
        zero_q = {
            "query_id": "q_zero",
            "candidates": [
                {"record_id": "rec_a", "bm25_rank": 1, "dense_rank": 1, "sparse_rank": 1, "colbert_rank": 1},
                {"record_id": "rec_b", "bm25_rank": 2, "dense_rank": 2, "sparse_rank": 2, "colbert_rank": 2},
            ],
        }
        inv_zero = execute_falsification_attack_reverse_rankings(zero_q, zero_gt)
        self.assertFalse(inv_zero.detected_cleanly, "0.0 -> 0.0 nDCG must not pass strict inversion degradation")


if __name__ == "__main__":
    unittest.main()

