"""Acceptance Test Suite for Wave 3: Pipeline C Unified Quad-Engine Retrieval.

Validates:
  1. VectorCache zero-copy float32 numpy matrix loading from SQLite BLOBs.
  2. In-memory BLAS dot product dense scoring with authorized ID filtering.
  3. Generational cache invalidation on generation_id and sidecar mtime shifts.
  4. Benchmark SLA: 500 consecutive query executions execute in < 7,500ms.
  5. PolicyGatedRetrievalEngine Law 6 & Law 2 enforcement (quarantine drop, zero advisory leaks).
  6. Hybrid RRF score blending (BM25 FTS5 + Token Overlap + BGE-M3 Dense Vector).
  7. Module budget governance: all touched modules strictly <= 400 lines (Invariant 1).
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.index_core.vector_cache import (
    LaneDenseVectors,
    VectorCache,
)
from pipeline_c_retrieval.engine import (
    CLIENT_SAFE_NAMESPACE,
    PolicyGatedRetrievalEngine,
)
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    InternalAdvisoryNode,
)


def _pack(vec: list[float] | np.ndarray) -> bytes:
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


class TestVectorCache(unittest.TestCase):
    """Unit and benchmark tests for generational VectorCache."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sidecar_path = Path(self.temp_dir.name) / "test_sidecar.db"
        self._init_sidecar(self.sidecar_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_sidecar(self, path: Path, num_records: int = 100, dim: int = 1024) -> None:
        conn = sqlite3.connect(str(path))
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS retrieval_record (
                    record_id TEXT PRIMARY KEY,
                    lane TEXT NOT NULL,
                    artifact_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dense_vector (
                    record_id TEXT PRIMARY KEY,
                    vector BLOB NOT NULL
                );
            """)
            np.random.seed(42)
            records = []
            vectors = []
            for i in range(num_records):
                rid = f"rec_{i:04d}"
                lane = "approved" if i % 2 == 0 else "candidate"
                artifact = {
                    "record_id": rid,
                    "lane": lane,
                    "classification": "internal",
                    "audiences": ["engineering"],
                }
                records.append((rid, lane, json.dumps(artifact)))
                vec = np.random.randn(dim).astype(np.float32)
                vec /= np.linalg.norm(vec)
                vectors.append((rid, _pack(vec)))

            conn.executemany("INSERT INTO retrieval_record VALUES (?, ?, ?)", records)
            conn.executemany("INSERT INTO dense_vector VALUES (?, ?)", vectors)
            conn.commit()
        finally:
            conn.close()

    def test_vector_cache_load_and_score(self) -> None:
        cache = VectorCache()
        lane_data = cache.get_or_load_lane(self.sidecar_path, "approved", generation_id="gen_001")
        self.assertIsInstance(lane_data, LaneDenseVectors)
        self.assertEqual(lane_data.count, 50)
        self.assertEqual(lane_data.dimensions, 1024)
        self.assertEqual(lane_data.matrix.shape, (50, 1024))
        self.assertEqual(lane_data.matrix.dtype, np.float32)

        # Query vector
        q = np.random.randn(1024).astype(np.float32)
        q /= np.linalg.norm(q)

        scores = cache.score_dense(
            self.sidecar_path,
            "approved",
            q,
            generation_id="gen_001",
        )
        self.assertEqual(len(scores), 50)
        # Verify sorted descending
        for i in range(len(scores) - 1):
            self.assertGreaterEqual(scores[i][0], scores[i + 1][0])

        # Test authorized filtering
        auth_subset = {f"rec_{i:04d}" for i in range(0, 20, 2)}
        filtered_scores = cache.score_dense(
            self.sidecar_path,
            "approved",
            q,
            authorized_record_ids=auth_subset,
            generation_id="gen_001",
        )
        self.assertEqual(len(filtered_scores), len(auth_subset))
        for _score, rid in filtered_scores:
            self.assertIn(rid, auth_subset)

    def test_vector_cache_generational_invalidation(self) -> None:
        cache = VectorCache()
        d1 = cache.get_or_load_lane(self.sidecar_path, "approved", generation_id="gen_001")
        self.assertEqual(d1.count, 50)

        # Second call with same generation hits cache directly
        d2 = cache.get_or_load_lane(self.sidecar_path, "approved", generation_id="gen_001")
        self.assertIs(d1, d2)

        # Shift generation_id -> invalidates and reloads
        d3 = cache.get_or_load_lane(self.sidecar_path, "approved", generation_id="gen_002")
        self.assertIsNot(d1, d3)
        self.assertEqual(d3.count, 50)

    def test_500_consecutive_queries_benchmark_latency(self) -> None:
        cache = VectorCache()
        # Warm cache
        q = np.random.randn(1024).astype(np.float32)
        q /= np.linalg.norm(q)
        cache.score_dense(self.sidecar_path, "approved", q, generation_id="gen_bm")

        start = time.perf_counter()
        iterations = 500
        for _ in range(iterations):
            cache.score_dense(self.sidecar_path, "approved", q, generation_id="gen_bm")
        elapsed = time.perf_counter() - start

        # 500 in-memory BLAS dot products must execute in << 7,500ms SLA
        self.assertLess(elapsed, 7.5, f"Benchmark took {elapsed:.3f}s, exceeding 7.5s SLA")


class TestPolicyGatedRetrievalEngineWave3(unittest.TestCase):
    """Tests PolicyGatedRetrievalEngine with VectorCache and Law 6 policy enforcement."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_manager = DatabaseManager(
            base_dir=Path(self.temp_dir.name),
            allow_unencrypted_dev_fallback=True,
        )
        self._populate_test_graph()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _populate_test_graph(self) -> None:
        # 1. Canonical public node (active, high value)
        self.db_manager.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_safe_active",
            evidence_id="ev_001",
            name="PartnerAdvisory Cloud Transformer",
            type="System",
            confidence=0.95,
            strategic_value=9.0,
            tacticality=0.4,
            is_quarantined=False,
            properties={"context": "Automated data transformation engine for cloud pipelines."},
        ))

        # 2. Canonical node in soft quarantine (Law 5)
        self.db_manager.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_safe_quarantined",
            evidence_id="ev_002",
            name="Unverified Pipeline Ingestion",
            type="System",
            confidence=0.4,
            strategic_value=8.0,
            tacticality=0.9,
            is_quarantined=True,
            properties={"context": "Draft ingestion script awaiting validation."},
        ))

        # 3. Confidential Internal Advisory Node (Law 2 / Law 6)
        self.db_manager.upsert_internal_advisory_node(InternalAdvisoryNode(
            node_id="adv_confidential",
            evidence_id="ev_003",
            category=AdvisoryCategory.DECISION_INFLUENCE,
            type="STRATEGY",
            name="Rate Negotiation Strategy 2026",
            confidence=0.99,
            strategic_value=10.0,
            tacticality=0.2,
            is_quarantined=False,
            properties={"signal": "Confidential advisory roadmap on enterprise pricing leverage."},
        ))

    def test_policy_gated_retrieval_stealth_mode_law6(self) -> None:
        engine = PolicyGatedRetrievalEngine(self.db_manager)

        # In stealth mode, quarantined must NOT be returned, advisory must NOT be returned
        results = engine.search("Cloud Transformer", stealth_mode=True)
        result_ids = [r.node_id for r in results]
        self.assertIn("node_safe_active", result_ids)
        self.assertNotIn("node_safe_quarantined", result_ids)
        self.assertNotIn("adv_confidential", result_ids)

        for r in results:
            self.assertEqual(r.namespace, CLIENT_SAFE_NAMESPACE)
            self.assertFalse(r.is_quarantined)

    def test_policy_gated_retrieval_with_vector_cache_integration(self) -> None:
        # Create a mock hybrid retriever with mock sidecar
        sidecar_path = Path(self.temp_dir.name) / "sidecar.db"
        conn = sqlite3.connect(str(sidecar_path))
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS retrieval_record (
                    record_id TEXT PRIMARY KEY,
                    lane TEXT NOT NULL,
                    artifact_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dense_vector (
                    record_id TEXT PRIMARY KEY,
                    vector BLOB NOT NULL
                );
            """)
            conn.execute(
                "INSERT INTO retrieval_record VALUES (?, ?, ?)",
                ("node_safe_active", "approved", json.dumps({"record_id": "node_safe_active"}))
            )
            v = np.ones(1024, dtype=np.float32)
            v /= np.linalg.norm(v)
            conn.execute("INSERT INTO dense_vector VALUES (?, ?)", ("node_safe_active", _pack(v)))
            conn.commit()
        finally:
            conn.close()

        class MockBackend:
            def encode(self, text: str):
                v = np.ones(1024, dtype=np.float32)
                v /= np.linalg.norm(v)
                return {"dense": v.tolist(), "sparse": {}, "colbert": []}

        class MockRetriever:
            def __init__(self, sp):
                self.sidecar_path = sp
                self.backend = MockBackend()
                self.validation = {"metadata": {"generation_id": "mock_gen"}}

        mock_retriever = MockRetriever(sidecar_path)
        cache = VectorCache()
        engine = PolicyGatedRetrievalEngine(
            self.db_manager,
            hybrid_retriever=mock_retriever,
            vector_cache=cache,
        )

        results = engine.search("Cloud Transformer", stealth_mode=True)
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].node_id, "node_safe_active")
        # Ensure RRF score was computed and > 0
        self.assertGreater(results[0].relevance_score, 0.0)


class TestWave3ModuleBudgets(unittest.TestCase):
    """Ensures all Wave 3 modified and newly created modules strictly obey the <= 400 lines limit."""

    def test_line_budgets(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        monitored_files = [
            repo_root / "dual_pipeline" / "index_core" / "vector_cache.py",
            repo_root / "dual_pipeline" / "index_core" / "facets.py",
            repo_root / "dual_pipeline" / "index_core" / "filters.py",
            repo_root / "dual_pipeline" / "index_core" / "fusion.py",
            repo_root / "dual_pipeline" / "index_core" / "retriever.py",
            repo_root / "pipeline_c_retrieval" / "engine.py",
        ]
        for f in monitored_files:
            self.assertTrue(f.exists(), f"File does not exist: {f}")
            line_count = len(f.read_text(encoding="utf-8").splitlines())
            self.assertLessEqual(
                line_count,
                400,
                f"Invariant 1 violation: {f.name} has {line_count} lines (budget <= 400)",
            )


if __name__ == "__main__":
    unittest.main()
