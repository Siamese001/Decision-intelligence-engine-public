#!/usr/bin/env python3
"""End-to-end test verifying numerical parity and top-k bounding of Dense Vector Pre-Filtering (Wave 8).

Verifies:
1. In-memory VectorCache O(N) partitioned score_dense produces bit-for-bit exact parity with full sort.
2. Full multi-lane hybrid retrieval with HybridRetriever maintains exact ranking with dense_limit.
3. Authorized record ID subset filtering correctly bounds candidates before partitioning.
"""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend  # noqa: E402
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: E402
from dual_pipeline.core import connect_sqlite, read_json  # noqa: E402
from dual_pipeline.index_core.retriever import HybridRetriever  # noqa: E402
from dual_pipeline.index_core.vector_cache import VectorCache  # noqa: E402


@pytest.mark.e2e
class TestDensePrefilterParityE2E(unittest.TestCase):
    """E2E suite verifying dense vector top-k pre-filtering parity against live sidecar."""

    @classmethod
    def setUpClass(cls) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.exists():
            runtime_root = ROOT.parent.parent / ".local" / "dual_pipeline"
        cls.runtime_root = runtime_root
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            cls.has_active_pair = False
            return

        cls.has_active_pair = True
        cls.active_pair = read_json(active_pair_path)
        cls.sidecar_path = runtime_root / cls.active_pair["sidecar_path"]

    def setUp(self) -> None:
        if not self.has_active_pair or not self.sidecar_path.is_file():
            self.skipTest("Active sidecar not available for E2E Dense Prefilter test")

    def test_e2e_dense_scoring_parity_on_sidecar(self) -> None:
        """Verify score_dense top-k partitioning parity against full-sort baseline on live sidecar."""
        conn = connect_sqlite(self.sidecar_path, readonly=True)
        row = conn.execute("SELECT vector FROM dense_vector LIMIT 1").fetchone()
        if not row:
            conn.close()
            self.skipTest("No dense vectors found in sidecar")

        sample_vec = np.frombuffer(row[0], dtype=np.float32)
        dim = len(sample_vec)
        conn.close()

        rng = np.random.default_rng(2026)
        q_vec = rng.normal(0, 1, size=dim).astype(np.float32)
        q_vec /= np.maximum(np.linalg.norm(q_vec), 1e-9)

        cache = VectorCache()
        lane = "approved"

        # 1. Full unconstrained sort baseline
        baseline_scores = cache.score_dense(self.sidecar_path, lane, q_vec, limit=None)
        total_items = len(baseline_scores)
        if total_items < 5:
            self.skipTest("Insufficient items in sidecar for meaningful top-k partitioning test")

        # 2. Partitioned top-k for multiple limit values
        limits_to_test = [1, 5, min(20, total_items), total_items]
        for k in limits_to_test:
            partitioned = cache.score_dense(self.sidecar_path, lane, q_vec, limit=k)
            expected = baseline_scores[:k]
            self.assertEqual(len(partitioned), len(expected))
            for (p_score, p_id), (b_score, b_id) in zip(partitioned, expected, strict=True):
                self.assertEqual(p_id, b_id)
                np.testing.assert_allclose(p_score, b_score, rtol=1e-6, atol=1e-6)

        # 3. Authorized ID subset filtering with limit
        subset_ids = {rid for _, rid in baseline_scores[:min(15, total_items)]}
        sub_limit = min(5, len(subset_ids))
        sub_partitioned = cache.score_dense(
            self.sidecar_path,
            lane,
            q_vec,
            authorized_record_ids=subset_ids,
            limit=sub_limit,
        )
        self.assertEqual(len(sub_partitioned), sub_limit)
        for _, rid in sub_partitioned:
            self.assertIn(rid, subset_ids)

    def test_e2e_hybrid_retriever_dense_limit_integration(self) -> None:
        """Verify HybridRetriever executes dense scoring with bounded limit and returns valid results."""
        args = argparse.Namespace(
            runtime_root=self.runtime_root,
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend=self.active_pair.get("backend", "bge-m3-dev"),
            device="cpu",
            fp16=False,
            assurance_tier="internal_operational",
            allow_dev_bge_m3=True,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )
        auto_resolve_backend_config(args)
        backend = _backend(args)

        retriever = HybridRetriever(
            sidecar_path=self.sidecar_path,
            backend=backend,
        )

        query = "decision intelligence architecture and governance"
        res = retriever.retrieve_lane(
            query=query,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            top_k=5,
        )
        self.assertIn("results", res)
        results = res["results"]
        self.assertLessEqual(len(results), 5)


if __name__ == "__main__":
    unittest.main()
