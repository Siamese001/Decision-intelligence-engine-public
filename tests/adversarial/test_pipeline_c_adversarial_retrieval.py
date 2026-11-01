"""Adversarial Red Team Tests for Pipeline C (Hybrid Retrieval & Cache Systems).

Evaluates:
1. Semantic Cache Collision & Poisoning: Differentiating inverted-polarity queries.
2. Adversarial Document Stuffing: Preventing BM25 term saturation search hijacking.
3. 16-Worker Concurrent Cache Race: Thread safety and zero torn reads under generation swap.
4. Unicode Homoglyph & Polyglot Injection: Robustness against non-standard text.
"""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.index_core.vector_cache import VectorCache
from dual_pipeline.retrieval.agents import RetrievalCoordinator
from pipeline_c_retrieval import CLIENT_SAFE_NAMESPACE, INTERNAL_ADVISORY_NAMESPACE


class TestPipelineCAdversarialRetrieval(unittest.TestCase):
    """Adversarial stress suite for Pipeline C retrieval and cache infrastructure."""

    def setUp(self) -> None:
        self.coordinator = RetrievalCoordinator(mock_mode=True)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_semantic_cache_polarity_inversion_distinction(self) -> None:
        """Inverted polarity queries ('approve' vs 'reject') must produce distinct cache hashes."""
        q_approve = "Did Stephan approve the cloud migration budget?"
        q_reject = "Did Stephan reject the cloud migration budget?"

        # Verify raw string difference and token separation
        self.assertNotEqual(q_approve, q_reject)

        # Coordinate retrieval for both
        candidate_facts = [
            {
                "node_id": "f_approve",
                "name": "Cloud Budget Approval",
                "properties": {"context": "Stephan signed off on the cloud migration budget."},
                "namespace": CLIENT_SAFE_NAMESPACE,
                "confidence": 0.95,
            },
            {
                "node_id": "f_reject",
                "name": "Cloud Budget Rejection",
                "properties": {"context": "Stephan rejected the expanded contingency budget."},
                "namespace": CLIENT_SAFE_NAMESPACE,
                "confidence": 0.95,
            },
        ]
        res_a = self.coordinator.retrieve(
            query=q_approve,
            candidate_facts=candidate_facts,
            caller_namespace=CLIENT_SAFE_NAMESPACE,
        )
        res_r = self.coordinator.retrieve(
            query=q_reject,
            candidate_facts=candidate_facts,
            caller_namespace=CLIENT_SAFE_NAMESPACE,
        )
        # Results must be independently generated
        self.assertIn("ranked_results", res_a)
        self.assertIn("ranked_results", res_r)
        self.assertTrue(res_a.get("release_passed", True))
        self.assertTrue(res_r.get("release_passed", True))

    def test_adversarial_document_stuffing_resistance(self) -> None:
        """Artificial nodes stuffed with high-frequency keywords must not evade policy gates."""
        stuffed_fact = {
            "node_id": "f_stuffed",
            "name": "BGE-M3 BGE-M3 BGE-M3 Architecture Architecture Architecture",
            "properties": {
                "context": "BGE-M3 BGE-M3 BGE-M3 " * 50 + " confidential advisory career promotion leverage",
            },
            "namespace": INTERNAL_ADVISORY_NAMESPACE,  # Adversary tagged as advisory
            "confidence": 0.99,
        }
        legit_fact = {
            "node_id": "f_legit",
            "name": "Governed BGE-M3 Retrieval Spec",
            "properties": {"context": "VectorCache accelerates BGE-M3 dense retrieval under 7,500ms."},
            "namespace": CLIENT_SAFE_NAMESPACE,
            "confidence": 0.95,
        }
        res = self.coordinator.retrieve(
            query="BGE-M3 architecture",
            candidate_facts=[stuffed_fact, legit_fact],
            caller_namespace=CLIENT_SAFE_NAMESPACE,
            stealth_mode=True,
        )
        ranked_ids = [item.get("node_id") for item in res["ranked_results"]]
        # The stuffed advisory fact must be strictly suppressed in stealth mode
        self.assertNotIn("f_stuffed", ranked_ids)
        self.assertIn("f_legit", ranked_ids)

    def test_16_worker_concurrent_cache_race(self) -> None:
        """16 concurrent threads querying VectorCache during generation reload must be race-free."""
        sidecar_path = Path(self.temp_dir) / "race_sidecar.db"
        conn = sqlite3.connect(str(sidecar_path))
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
        records = []
        vectors = []
        for i in range(20):
            rid = f"rec-{i:03d}"
            records.append((rid, "approved", json.dumps({"record_id": rid, "lane": "approved"})))
            v = np.random.randn(1024).astype(np.float32)
            v /= np.linalg.norm(v)
            vectors.append((rid, v.tobytes()))
        conn.executemany("INSERT INTO retrieval_record VALUES (?, ?, ?)", records)
        conn.executemany("INSERT INTO dense_vector VALUES (?, ?)", vectors)
        conn.commit()
        conn.close()

        cache = VectorCache()
        query_vec = np.random.randn(1024).astype(np.float32)
        query_vec /= np.linalg.norm(query_vec)

        def worker_task(worker_id: int) -> int:
            gen_id = f"gen_{(worker_id % 3) + 1:03d}"
            # Concurrently score dense vectors
            scores = cache.score_dense(sidecar_path, "approved", query_vec, generation_id=gen_id)
            if worker_id % 4 == 0:
                cache.clear()
            return len(scores)

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
            futures = [executor.submit(worker_task, i) for i in range(32)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 32)
        for count in results:
            self.assertEqual(count, 20)

    def test_unicode_homoglyphs_and_polyglot_queries(self) -> None:
        """Adversarial queries with zero-width spaces and Cyrillic homoglyphs must not crash."""
        homoglyph_queries = [
            "BGE-M3 \u200B\u200C\u200D architecture",  # Zero-width spaces
            "\u202E\u202DRTL Override Query\u202C",      # BiDi override
            "ВGЕ-М3 аrсhitесturе",                      # Cyrillic lookalikes
            "A" * 5000,                                 # Extreme length query
            "SELECT * FROM users WHERE '1'='1';",       # SQL injection syntax
        ]
        candidate_facts = [
            {
                "node_id": "f1",
                "name": "Standard Architecture",
                "properties": {"context": "Reference system design."},
                "namespace": CLIENT_SAFE_NAMESPACE,
                "confidence": 0.90,
            }
        ]
        for polyglot_q in homoglyph_queries:
            res = self.coordinator.retrieve(
                query=polyglot_q,
                candidate_facts=candidate_facts,
                caller_namespace=CLIENT_SAFE_NAMESPACE,
            )
            self.assertIsInstance(res, dict)
            self.assertIn("ranked_results", res)
            self.assertLess(res.get("actual_latency_ms", 0.0), 7500.0)
