#!/usr/bin/env python3
"""Wave 4 Acceptance Test Suite: Cross-Platform Retrieval Portability, Scale & Multi-Tenant Clearance.

Validates:
  1. Cross-platform BGE-M3 path resolution & environment variable precedence.
  2. Runtime root, release receipt, and suite discovery across platforms.
  3. Dynamic query-intent classification (EXACT_KEYWORD, SEMANTIC_NARRATIVE, HYBRID_STANDARD).
  4. Dynamic weight rebalancing strictly adhering to the 4-simplex constraint (sum = 1.0).
  5. PolicyGatedRetrievalEngine Law 6 & Law 2 multi-tenant stealth mode isolation (zero advisory leaks).
  6. PolicyGatedRetrievalEngine soft-quarantine suppression (zero tactical leaks).
  7. High-concurrency retrieval stress test and latency SLAs (p95 <= 150ms, max <= 7500ms).
  8. Calibrated 11-Gate retrieval release evaluation receipt verification.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

from evals.evaluate_11gates_calibrated import (
    PINNED_REVISION,
    resolve_bge_m3_paths,
    resolve_runtime_and_artifacts,
)
from evals.evaluate_retrieval_concurrency_and_scale import (
    populate_benchmark_graph,
    run_retrieval_concurrency_evaluation,
)
from evals.qrels_weight_calibration import (
    calculate_dynamic_query_weights,
    classify_query_intent,
    evaluate_dynamic_weight_configuration,
)
from pipeline_c_retrieval import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    DatabaseManager,
)


class TestWave4CrossPlatformPaths(unittest.TestCase):
    """Tests cross-platform BGE-M3 model, manifest, and runtime resolution."""

    def test_pinned_revision_constant(self) -> None:
        self.assertEqual(PINNED_REVISION, "5617a9f61b028005a4858fdac845db406aefb181")

    def test_resolve_bge_m3_paths_with_explicit_args(self) -> None:
        custom_model = Path("/tmp/custom_models/bge-m3")
        custom_manifest = Path("/tmp/custom_models/manifest.json")
        model, manifest, rev = resolve_bge_m3_paths(
            model_path_arg=custom_model,
            manifest_path_arg=custom_manifest,
        )
        self.assertEqual(model, custom_model)
        self.assertEqual(manifest, custom_manifest)
        self.assertEqual(rev, PINNED_REVISION)

    def test_resolve_bge_m3_paths_from_env_vars(self) -> None:
        with mock.patch.dict(os.environ, {
            "NEE_BGE_M3_MODEL_PATH": "/tmp/env_model",
            "NEE_BGE_M3_MODEL_MANIFEST": "/tmp/env_manifest.json",
            "BGE_M3_MODEL_PATH": "/tmp/env_model",
            "BGE_M3_MANIFEST_PATH": "/tmp/env_manifest.json",
        }):
            model, manifest, rev = resolve_bge_m3_paths()
            self.assertEqual(model, Path("/tmp/env_model"))
            self.assertEqual(manifest, Path("/tmp/env_manifest.json"))
            self.assertEqual(rev, PINNED_REVISION)

        # Test fallback when NEE_ prefix is absent
        with mock.patch.dict(os.environ, {
            "BGE_M3_MODEL_PATH": "/tmp/bge_model",
            "BGE_M3_MANIFEST_PATH": "/tmp/bge_manifest.json",
        }, clear=True):
            model, manifest, rev = resolve_bge_m3_paths()
            self.assertEqual(model, Path("/tmp/bge_model"))
            self.assertEqual(manifest, Path("/tmp/bge_manifest.json"))
            self.assertEqual(rev, PINNED_REVISION)

    def test_resolve_bge_m3_paths_local_repo_fallback(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            model, manifest, rev = resolve_bge_m3_paths()
            expected_local_model = ROOT / "models" / "bge-m3" / PINNED_REVISION
            expected_local_manifest = ROOT / "models" / "bge-m3" / f"bge-m3-manifest-{PINNED_REVISION}.json"
            if expected_local_model.exists():
                self.assertEqual(model, expected_local_model)
            if expected_local_manifest.exists():
                self.assertEqual(manifest, expected_local_manifest)
            self.assertEqual(rev, PINNED_REVISION)

    def test_resolve_runtime_and_artifacts_resolution(self) -> None:
        runtime_root, receipt_path, suite_path = resolve_runtime_and_artifacts()
        self.assertTrue(runtime_root.exists(), f"Runtime root does not exist: {runtime_root}")
        self.assertTrue(receipt_path.exists(), f"Receipt path does not exist: {receipt_path}")
        self.assertTrue(suite_path.exists(), f"Suite path does not exist: {suite_path}")


class TestWave4DynamicQueryIntentWeights(unittest.TestCase):
    """Tests query intent classification and dynamic 4-simplex weight calculation."""

    def test_exact_keyword_intent_classification(self) -> None:
        exact_queries = [
            "c_wal_dec",
            "PO2000622222",
            '"WAL Journal Mode"',
            "'Architecture baseline'",
            "FTS5",
            "BGE_M3",
            "urn:nee:dual-pipeline:retrieval-record:1.0.0",
        ]
        for q in exact_queries:
            intent = classify_query_intent(q)
            self.assertEqual(intent, "EXACT_KEYWORD", f"Failed for query: {q}")

    def test_semantic_narrative_intent_classification(self) -> None:
        narrative_queries = [
            "What is the rationale behind adopting the WAL journal mode for SQLite?",
            "Why was the database compute fork decoupled from the extraction path?",
            "Explain how the multi-tenant stealth mode firewall isolates advisory notes",
            "Describe the overall architecture for the Project Beacon platform",
            "What are the key technical dependencies and operational risks?",
        ]
        for q in narrative_queries:
            intent = classify_query_intent(q)
            self.assertEqual(intent, "SEMANTIC_NARRATIVE", f"Failed for query: {q}")

    def test_hybrid_standard_intent_classification(self) -> None:
        hybrid_queries = [
            "Outage Communication App",
            "Cloud data pipeline",
            "Meeting synthesis notes",
            "Executive recap summary",
        ]
        for q in hybrid_queries:
            intent = classify_query_intent(q)
            self.assertEqual(intent, "HYBRID_STANDARD", f"Failed for query: {q}")

    def test_dynamic_weights_simplex_constraint(self) -> None:
        test_queries = [
            "c_wal_dec",
            "What is the architecture rationale?",
            "Outage Communication App",
            "",
            "   ",
        ]
        for q in test_queries:
            weights = calculate_dynamic_query_weights(q)
            weight_sum = sum(weights.values())
            self.assertAlmostEqual(weight_sum, 1.0, places=3, msg=f"Simplex violated for: {q}")
            self.assertIn("dense", weights)
            self.assertIn("sparse", weights)
            self.assertIn("bm25", weights)
            self.assertIn("colbert", weights)

    def test_exact_keyword_boosts_bm25(self) -> None:
        weights = calculate_dynamic_query_weights("c_wal_dec")
        self.assertGreaterEqual(weights["bm25"], 0.70)
        self.assertLessEqual(weights["dense"], 0.10)

    def test_semantic_narrative_boosts_dense_and_colbert(self) -> None:
        weights = calculate_dynamic_query_weights("Why did the committee decide on SQLite WAL mode?")
        self.assertGreaterEqual(weights["dense"], 0.35)
        self.assertGreaterEqual(weights["colbert"], 0.25)
        self.assertLessEqual(weights["bm25"], 0.20)

    def test_evaluate_dynamic_weight_configuration_on_mock_pool(self) -> None:
        mock_queries = [
            {
                "query_text": "c_wal_dec",
                "judged_candidates": [
                    {"grade": 2, "bm25_rank": 0, "dense_rank": 5, "sparse_rank": 1, "colbert_rank": 4},
                    {"grade": 1, "bm25_rank": 1, "dense_rank": 2, "sparse_rank": 3, "colbert_rank": 2},
                    {"grade": 0, "bm25_rank": 5, "dense_rank": 0, "sparse_rank": 0, "colbert_rank": 0},
                ],
            },
            {
                "query_text": "What is the rationale behind adopting the WAL journal mode?",
                "judged_candidates": [
                    {"grade": 2, "bm25_rank": 4, "dense_rank": 0, "sparse_rank": 1, "colbert_rank": 0},
                    {"grade": 1, "bm25_rank": 2, "dense_rank": 1, "sparse_rank": 2, "colbert_rank": 1},
                    {"grade": 0, "bm25_rank": 0, "dense_rank": 4, "sparse_rank": 4, "colbert_rank": 5},
                ],
            },
        ]
        ndcg, mrr = evaluate_dynamic_weight_configuration(mock_queries)
        self.assertGreater(ndcg, 0.80)
        self.assertGreater(mrr, 0.80)


class TestWave4RetrievalConcurrencyAndScale(unittest.TestCase):
    """Tests PolicyGatedRetrievalEngine multi-tenant security and concurrent retrieval SLAs."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee-wave4-test-"))
        self.db_mgr = DatabaseManager(base_dir=self.temp_dir, allow_unencrypted_dev_fallback=True)
        populate_benchmark_graph(self.db_mgr, num_canonical=200, num_advisory=100)
        self.engine = PolicyGatedRetrievalEngine(db_manager=self.db_mgr)

    def tearDown(self) -> None:
        self.db_mgr.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_stealth_mode_zero_advisory_leakage(self) -> None:
        """Law 2 & Law 6: Queries with stealth_mode=True NEVER leak INTERNAL_ADVISORY facts."""
        results = self.engine.search(
            query="Executive Leadership Alignment",
            stealth_mode=True,
            limit=20,
        )
        for r in results:
            self.assertEqual(r.namespace, CLIENT_SAFE_NAMESPACE)
            self.assertNotEqual(r.namespace, INTERNAL_ADVISORY_NAMESPACE)
            self.assertNotIn("Confidential", r.snippet)

    def test_soft_quarantine_node_suppression(self) -> None:
        """Law 5: Quarantined nodes are excluded by default."""
        results = self.engine.search(
            query="Tactical Note",
            stealth_mode=True,
            include_quarantined=False,
            limit=20,
        )
        for r in results:
            self.assertFalse(r.is_quarantined)

    def test_unmasked_advisory_mode_includes_both_namespaces(self) -> None:
        """When stealth_mode=False, advisory facts are safely visible."""
        results = self.engine.search(
            query="Executive Leadership Alignment",
            stealth_mode=False,
            limit=20,
        )
        namespaces = {r.namespace for r in results}
        self.assertIn(INTERNAL_ADVISORY_NAMESPACE, namespaces)

    def test_concurrent_retrieval_benchmark_and_latency_sla(self) -> None:
        """Executes concurrent queries measuring throughput, p95 <= 150ms, max <= 7500ms."""
        receipt = run_retrieval_concurrency_evaluation(
            num_workers=10,
            num_queries=30,
        )
        self.assertEqual(receipt["status"], "PASS")
        self.assertLessEqual(receipt["latency_distribution_ms"]["p95"], 150.0)
        self.assertLessEqual(receipt["latency_distribution_ms"]["max"], 7500.0)
        self.assertEqual(receipt["security_and_filtering"]["cross_tenant_leaks"], 0)
        self.assertEqual(receipt["security_and_filtering"]["quarantine_leaks"], 0)
        self.assertEqual(receipt["security_and_filtering"]["error_count"], 0)


class TestWave4Calibrated11GateReceipt(unittest.TestCase):
    """Verifies that the calibrated 11-gate release receipt exists and satisfies all gates."""

    def test_wave4_11gate_receipt_validity(self) -> None:
        receipt_path = ROOT / "evals" / "wave4_11gate_release_evaluation_receipt.json"
        self.assertTrue(receipt_path.exists(), f"Receipt missing: {receipt_path}")

        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["evaluation_contract"], "urn:nee:dual-pipeline:retrieval-release-evaluation:1.0.0")

        gates = receipt["gates"]
        self.assertEqual(len(gates), 11, f"Expected 11 gates, found {len(gates)}")
        for gate_name, passed in gates.items():
            self.assertTrue(passed, f"Gate failed: {gate_name}")

        agg = receipt["aggregate"]
        self.assertEqual(agg["core_recall"], 1.0)
        self.assertEqual(agg["core_mrr"], 1.0)
        self.assertEqual(agg["core_ndcg"], 1.0)
        self.assertEqual(agg["delta_recall"], 1.0)
        self.assertEqual(agg["total_leakage"], 0)
        self.assertEqual(agg["total_rank_regressions"], 0)
        self.assertEqual(agg["total_repeat_drift"], 0)
        self.assertEqual(agg["critical_failures"], 0)
        self.assertLessEqual(agg["max_latency_ms"], 7500.0)


if __name__ == "__main__":
    unittest.main()
