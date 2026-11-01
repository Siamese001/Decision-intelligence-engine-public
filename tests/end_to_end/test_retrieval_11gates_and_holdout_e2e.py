"""E2E tests for Retrieval 11-Gate Verification, Holdout Isolation & Canonical Fusion (Wave 2).

Verifies:
1. compute_11gates dynamically computes Gates 1, 2, 3, and 11.
2. Dropping any retrieval engine flips Gate 11 (and Gate 1) to False.
3. Model binding mismatch flips Gate 2 (pair_binding) to False.
4. Incomplete record coverage flips Gate 3 (coverage_complete) to False.
5. qrels_weight_calibration isolates final_holdout queries so calibration uses only dev+calibration.
6. Calibration output confirms strictly disjoint calibration vs final_holdout query IDs.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evals.evaluate_11gates_calibrated import compute_11gates
from evals.qrels_weight_calibration import (
    evaluate_weight_configuration,
    run_weight_calibration,
)
from dual_pipeline.index import BM25_RRF_WEIGHTS


class DummyBackend:
    def __init__(self, backend_id: str = "bge-m3", model_id: str = "BAAI/bge-m3", revision: str = "rev123"):
        self.backend_id = backend_id
        self.model_id = model_id
        self.model_revision = revision


class TestRetrieval11GatesAndHoldoutE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_retrieval_11gates_e2e_")
        self.tmp_path = Path(self.temp_dir)

        # Create dummy sidecar and graph files
        self.base_sidecar = self.tmp_path / "base.sidecar"
        self.cand_sidecar = self.tmp_path / "cand.sidecar"
        self.base_graph = self.tmp_path / "base.sqlite"
        self.cand_graph = self.tmp_path / "cand.sqlite"
        for p in (self.base_sidecar, self.cand_sidecar, self.base_graph, self.cand_graph):
            p.write_bytes(b"dummy data")

        self.valid_receipt = {"receipt_id": "rcpt-001"}
        self.valid_baseline_pair = {
            "model_id": "BAAI/bge-m3",
            "model_revision": "rev123",
        }
        self.valid_candidate_pair = {
            "model_id": "BAAI/bge-m3",
            "model_revision": "rev123",
        }
        self.thresholds = {"max_latency_ms": 7500.0, "critical_top_k": 5}
        self.agg = {
            "core_recall": 1.0,
            "core_mrr": 1.0,
            "core_ndcg": 1.0,
            "delta_recall": 1.0,
            "delta_mrr": 1.0,
            "delta_ndcg": 1.0,
            "critical_failures": 0,
            "total_rank_regressions": 0,
            "total_repeat_drift": 0,
            "total_leakage": 0,
            "max_latency_ms": 50.0,
        }

    def _make_case_results(self, engines: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        comp = engines or {"dense": True, "sparse": True, "bm25": True, "colbert": True}
        return [
            {
                "case_id": "case-01",
                "component_runs": [[dict(comp)]],
            }
        ]

    def test_all_11_gates_pass_on_valid_inputs(self) -> None:
        """All 11 gates must dynamically evaluate to True when all requirements pass."""
        backend = DummyBackend(revision="rev123")
        gates = compute_11gates(
            backend=backend,
            revision="rev123",
            baseline_pair=self.valid_baseline_pair,
            candidate_pair=self.valid_candidate_pair,
            baseline_sidecar=self.base_sidecar,
            candidate_sidecar=self.cand_sidecar,
            baseline_graph=self.base_graph,
            candidate_graph=self.cand_graph,
            receipt=self.valid_receipt,
            coverage_complete=True,
            case_results=self._make_case_results(),
            agg=self.agg,
            thresholds=self.thresholds,
        )
        for g_name, passed in gates.items():
            self.assertTrue(passed, f"Gate '{g_name}' should have passed dynamically")

    def test_dropping_any_engine_flips_gate11_and_gate1_to_false(self) -> None:
        """Dropping dense, sparse, bm25, or colbert must cause Gate 11 and Gate 1 to fail."""
        backend = DummyBackend(revision="rev123")
        for engine in ("dense", "sparse", "bm25", "colbert"):
            degraded_engines = {"dense": True, "sparse": True, "bm25": True, "colbert": True}
            degraded_engines[engine] = False
            case_results = self._make_case_results(degraded_engines)

            gates = compute_11gates(
                backend=backend,
                revision="rev123",
                baseline_pair=self.valid_baseline_pair,
                candidate_pair=self.valid_candidate_pair,
                baseline_sidecar=self.base_sidecar,
                candidate_sidecar=self.cand_sidecar,
                baseline_graph=self.base_graph,
                candidate_graph=self.cand_graph,
                receipt=self.valid_receipt,
                coverage_complete=True,
                case_results=case_results,
                agg=self.agg,
                thresholds=self.thresholds,
            )
            self.assertFalse(
                gates["11. component_execution (all 4 engines)"],
                f"Gate 11 must flip to False when engine '{engine}' is dropped",
            )
            self.assertFalse(
                gates["1. real_bge_m3"],
                f"Gate 1 must flip to False when component execution fails for '{engine}'",
            )

    def test_model_binding_mismatch_flips_gate2_to_false(self) -> None:
        """Pair model revision mismatch must fail Gate 2 (pair_binding)."""
        backend = DummyBackend(revision="rev123")
        mismatched_cand_pair = dict(self.valid_candidate_pair)
        mismatched_cand_pair["model_revision"] = "different_rev"

        gates = compute_11gates(
            backend=backend,
            revision="rev123",
            baseline_pair=self.valid_baseline_pair,
            candidate_pair=mismatched_cand_pair,
            baseline_sidecar=self.base_sidecar,
            candidate_sidecar=self.cand_sidecar,
            baseline_graph=self.base_graph,
            candidate_graph=self.cand_graph,
            receipt=self.valid_receipt,
            coverage_complete=True,
            case_results=self._make_case_results(),
            agg=self.agg,
            thresholds=self.thresholds,
        )
        self.assertFalse(gates["2. pair_binding"], "Gate 2 must fail on model revision mismatch")

    def test_coverage_incomplete_flips_gate3_to_false(self) -> None:
        """Incomplete coverage must fail Gate 3 (coverage_complete)."""
        backend = DummyBackend(revision="rev123")
        gates = compute_11gates(
            backend=backend,
            revision="rev123",
            baseline_pair=self.valid_baseline_pair,
            candidate_pair=self.valid_candidate_pair,
            baseline_sidecar=self.base_sidecar,
            candidate_sidecar=self.cand_sidecar,
            baseline_graph=self.base_graph,
            candidate_graph=self.cand_graph,
            receipt=self.valid_receipt,
            coverage_complete=False,
            case_results=self._make_case_results(),
            agg=self.agg,
            thresholds=self.thresholds,
        )
        self.assertFalse(gates["3. coverage_complete"], "Gate 3 must fail when coverage is incomplete")

    def test_qrels_calibration_isolates_final_holdout_queries(self) -> None:
        """qrels_weight_calibration must strictly exclude final_holdout queries from calibration."""
        judgments_file = self.tmp_path / "test_judgments.json"
        partitions_file = self.tmp_path / "test_partitions.json"
        output_file = self.tmp_path / "test_calibration_out.json"

        # Create mock queries: 2 dev, 1 calibration, 2 final_holdout
        sample_queries = [
            {
                "query_id": "q_dev_1",
                "query_text": "dev query 1",
                "judged_candidates": [{"grade": 2, "bm25_rank": 1, "dense_rank": 1, "sparse_rank": 1, "colbert_rank": 1}],
            },
            {
                "query_id": "q_dev_2",
                "query_text": "dev query 2",
                "judged_candidates": [{"grade": 1, "bm25_rank": 2, "dense_rank": 1, "sparse_rank": 2, "colbert_rank": 1}],
            },
            {
                "query_id": "q_cal_1",
                "query_text": "cal query 1",
                "judged_candidates": [{"grade": 3, "bm25_rank": 1, "dense_rank": 2, "sparse_rank": 1, "colbert_rank": 2}],
            },
            {
                "query_id": "q_holdout_1",
                "query_text": "holdout query 1",
                "judged_candidates": [{"grade": 3, "bm25_rank": 1, "dense_rank": 1, "sparse_rank": 1, "colbert_rank": 1}],
            },
            {
                "query_id": "q_holdout_2",
                "query_text": "holdout query 2",
                "judged_candidates": [{"grade": 2, "bm25_rank": 1, "dense_rank": 2, "sparse_rank": 2, "colbert_rank": 1}],
            },
        ]
        judgments_file.write_text(json.dumps({"evaluated_queries": sample_queries}), encoding="utf-8")

        partitions = {
            "query_partitions": {
                "dev": ["q_dev_1", "q_dev_2"],
                "calibration": ["q_cal_1"],
                "final_holdout": ["q_holdout_1", "q_holdout_2"],
            }
        }
        partitions_file.write_text(json.dumps(partitions), encoding="utf-8")

        payload = run_weight_calibration(
            judgments_path=judgments_file,
            output_path=output_file,
            grid_step=0.25,  # Faster grid step for test
            partitions_path=partitions_file,
        )

        # Independent check: Confirm disjoint query IDs in output JSON
        calibration_ids = set(payload["calibration_query_ids"])
        holdout_ids = set(payload["excluded_holdout_query_ids"])

        self.assertEqual(len(calibration_ids), 3)
        self.assertEqual(len(holdout_ids), 2)
        self.assertTrue(
            calibration_ids.isdisjoint(holdout_ids),
            "Calibration queries and holdout queries must be strictly disjoint",
        )
        self.assertNotIn("q_holdout_1", calibration_ids)
        self.assertNotIn("q_holdout_2", calibration_ids)
        self.assertEqual(payload["partition_strategy"], "dev_plus_calibration_strict")

    def test_bge_m3_dev_backend_fails_gate1(self) -> None:
        """Gate 1 must strictly reject 'bge-m3-dev' in favor of production 'bge-m3'."""
        backend = DummyBackend(backend_id="bge-m3-dev", revision="rev123")
        gates = compute_11gates(
            backend=backend,
            revision="rev123",
            baseline_pair=self.valid_baseline_pair,
            candidate_pair=self.valid_candidate_pair,
            baseline_sidecar=self.base_sidecar,
            candidate_sidecar=self.cand_sidecar,
            baseline_graph=self.base_graph,
            candidate_graph=self.cand_graph,
            receipt=self.valid_receipt,
            coverage_complete=True,
            case_results=self._make_case_results(),
            agg=self.agg,
            thresholds=self.thresholds,
        )
        self.assertFalse(gates["1. real_bge_m3"], "Gate 1 must reject 'bge-m3-dev'")

    def test_missing_metrics_in_agg_fails_closed(self) -> None:
        """Missing core metrics or critical failure counts in agg must fail closed."""
        incomplete_agg = {"core_recall": 1.0}  # missing mrr, ndcg, critical_failures, etc.
        backend = DummyBackend(backend_id="bge-m3", revision="rev123")
        gates = compute_11gates(
            backend=backend,
            revision="rev123",
            baseline_pair=self.valid_baseline_pair,
            candidate_pair=self.valid_candidate_pair,
            baseline_sidecar=self.base_sidecar,
            candidate_sidecar=self.cand_sidecar,
            baseline_graph=self.base_graph,
            candidate_graph=self.cand_graph,
            receipt=self.valid_receipt,
            coverage_complete=True,
            case_results=self._make_case_results(),
            agg=incomplete_agg,
            thresholds=self.thresholds,
        )
        self.assertFalse(gates["4. core_thresholds (Recall=1, MRR=1, NDCG=1)"])
        self.assertFalse(gates["6. critical_cases (0 failures)"])


if __name__ == "__main__":
    unittest.main()
