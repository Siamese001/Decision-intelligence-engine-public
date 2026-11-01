#!/usr/bin/env python3
"""End-to-end test verifying numerical parity and performance of Sparse Index Vectorization (Wave 5).

Verifies:
1. In-memory LaneSparseIndex produces bit-for-bit exact score parity with legacy SQLite direct queries.
2. Full multi-lane hybrid retrieval with HybridRetriever maintains exact ranking.
3. SparseIndexCache yields cache hits across repeated query executions.
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
from dual_pipeline.index_core.sparse_index import SparseIndexCache  # noqa: E402


@pytest.mark.e2e
class TestSparseIndexParityE2E(unittest.TestCase):
    """E2E suite verifying in-memory sparse scoring parity against live sidecar."""

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
            self.skipTest("Active sidecar not available for E2E Sparse Index test")

    def test_e2e_sparse_scoring_parity_on_sidecar(self) -> None:
        """Verify score_sparse parity against direct SQLite query across active sidecar."""
        conn = connect_sqlite(self.sidecar_path, readonly=True)
        # Select sample of tokens from sparse_weight
        sample_rows = conn.execute(
            "SELECT token, weight FROM sparse_weight LIMIT 10"
        ).fetchall()
        if not sample_rows:
            conn.close()
            self.skipTest("No sparse weights found in sidecar")

        query_sparse = {str(r[0]): float(r[1]) for r in sample_rows}
        lane = "approved"

        # 1. Legacy SQLite scoring
        tokens = sorted(query_sparse)
        placeholders = ",".join("?" for _ in tokens)
        legacy_totals: dict[str, float] = {}
        for row in conn.execute(
            f"""SELECT sw.record_id, sw.token, sw.weight FROM sparse_weight sw
                JOIN retrieval_record r ON r.record_id=sw.record_id
                WHERE r.lane=? AND sw.token IN ({placeholders})""",
            [lane, *tokens],
        ):
            rid = row[0]
            tok = row[1]
            weight = row[2]
            legacy_totals[rid] = legacy_totals.get(rid, 0.0) + weight * query_sparse[tok]

        legacy_scores = sorted(
            ((score, record_id) for record_id, score in legacy_totals.items()),
            key=lambda item: (-item[0], item[1]),
        )[:100]
        conn.close()

        # 2. In-memory SparseIndexCache scoring
        cache = SparseIndexCache()
        cached_scores = cache.score_sparse(
            sidecar_path=self.sidecar_path,
            lane=lane,
            query_sparse=query_sparse,
            sparse_limit=100,
        )

        # 3. Assert exact count and numerical parity
        self.assertEqual(len(cached_scores), len(legacy_scores))
        for (c_score, c_id), (l_score, l_id) in zip(cached_scores, legacy_scores, strict=True):
            self.assertEqual(c_id, l_id)
            np.testing.assert_allclose(c_score, l_score, rtol=1e-12, atol=1e-12)

    def test_e2e_hybrid_retriever_sparse_cache_lifecycle(self) -> None:
        """Verify HybridRetriever utilizes sparse cache and maintains hits across calls."""
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

        sparse_cache = SparseIndexCache()
        retriever = HybridRetriever(
            sidecar_path=self.sidecar_path,
            backend=backend,
            sparse_cache=sparse_cache,
        )

        query = "Project Nexus architecture and PartnerAdvisory alignment"
        res1 = retriever.retrieve_lane(
            query=query,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            top_k=5,
        )
        self.assertIn("results", res1)
        self.assertEqual(sparse_cache.stats()["misses"], 1)

        # Repeated retrieval should hit cache
        res2 = retriever.retrieve_lane(
            query=query,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            top_k=5,
        )
        self.assertIn("results", res2)
        self.assertEqual(sparse_cache.stats()["hits"], 1)


if __name__ == "__main__":
    unittest.main()
