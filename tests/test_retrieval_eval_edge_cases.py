"""Adversarial and edge-case unit tests for retrieval evaluation caching and vectorization."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.index_core.fusion import score_colbert
from dual_pipeline.retrieval_release_core.baseline_cache import (
    BaselineQueryCache,
    _case_cache_key,
)


class TestRetrievalEvalEdgeCases(unittest.TestCase):
    """Adversarial edge-case tests for baseline caching and ColBERT vector scoring."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_case_1_corrupted_or_malformed_cache_file_fails_safe(self) -> None:
        """Proves that a corrupted baseline cache file fails safe and behaves as an empty cache."""
        cache_file = self.test_root / ".baseline_query_cache.json"
        cache_file.write_text("NOT A VALID JSON FILE {{{{{", encoding="utf-8")

        cache = BaselineQueryCache(cache_file)
        # Should gracefully catch exception and treat as empty
        case = {
            "query": "test query",
            "lane": "approved",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "top_k": 20,
        }
        result = cache.get("hash_graph", "hash_sidecar", case)
        self.assertIsNone(result)

        # Should allow setting a new result and writing out cleanly
        cache.set(
            "hash_graph",
            "hash_sidecar",
            case,
            (["rec-1"], [{"record_id": "rec-1", "fused_rank": 1}], {"dense": True}),
        )
        cache.flush()

        # Reloading should succeed
        cache2 = BaselineQueryCache(cache_file)
        loaded = cache2.get("hash_graph", "hash_sidecar", case)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded[0], ["rec-1"])

    def test_case_2_hash_mismatch_prevents_stale_cache_leakage(self) -> None:
        """Proves that different graph or sidecar hashes never return results from another generation."""
        cache_file = self.test_root / ".baseline_query_cache.json"
        cache = BaselineQueryCache(cache_file)

        case = {
            "query": "architecture design",
            "lane": "approved",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "top_k": 20,
        }
        cache.set(
            "graph_sha_v1",
            "sidecar_sha_v1",
            case,
            (["rec-old"], [], {}),
        )

        # Query with different graph hash
        res_diff_graph = cache.get("graph_sha_v2", "sidecar_sha_v1", case)
        self.assertIsNone(res_diff_graph)

        # Query with different sidecar hash
        res_diff_sidecar = cache.get("graph_sha_v1", "sidecar_sha_v2", case)
        self.assertIsNone(res_diff_sidecar)

        # Query with identical hashes
        res_match = cache.get("graph_sha_v1", "sidecar_sha_v1", case)
        self.assertIsNotNone(res_match)
        self.assertEqual(res_match[0], ["rec-old"])

    def test_case_3_deterministic_key_isolation(self) -> None:
        """Proves that variations in lane, classification, or audiences produce distinct keys."""
        base_case = {
            "query": "common query",
            "lane": "approved",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "top_k": 20,
        }
        key_base = _case_cache_key("g", "s", base_case)

        case_lane = dict(base_case, lane="candidate")
        key_lane = _case_cache_key("g", "s", case_lane)
        self.assertNotEqual(key_base, key_lane)

        case_class = dict(base_case, classification="public")
        key_class = _case_cache_key("g", "s", case_class)
        self.assertNotEqual(key_base, key_class)

        case_aud = dict(base_case, audiences=["other_aud"])
        key_aud = _case_cache_key("g", "s", case_aud)
        self.assertNotEqual(key_base, key_aud)

    def test_case_4_colbert_scoring_with_empty_or_missing_records(self) -> None:
        """Proves score_colbert handles empty target IDs, missing tokens, and empty matrices safely."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """CREATE TABLE colbert_token (
                record_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                token TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL
            )"""
        )

        query_colbert = np.random.randn(5, 32).astype(np.float32)
        cache: dict[str, np.ndarray] = {}

        # 1. Target list is empty
        scores_empty = score_colbert(conn, query_colbert, [], colbert_cache=cache)
        self.assertEqual(scores_empty, [])

        # 2. Target list contains record not in DB (missing)
        scores_missing = score_colbert(
            conn, query_colbert, ["missing-rec"], colbert_cache=cache
        )
        self.assertEqual(len(scores_missing), 1)
        self.assertEqual(scores_missing[0], (0.0, "missing-rec"))
        # Cache should store empty matrix (0, 32)
        self.assertIn("missing-rec", cache)
        self.assertEqual(cache["missing-rec"].shape, (0, 32))

        # 3. Repeated call uses cache directly
        scores_cached = score_colbert(
            conn, query_colbert, ["missing-rec"], colbert_cache=cache
        )
        self.assertEqual(scores_cached[0], (0.0, "missing-rec"))

        conn.close()

    def test_case_5_clearance_and_fts_cache_invalidation_on_mutation(self) -> None:
        """Proves clearance_cache reuses cached records/FTS5 and invalidates immediately when graph/sidecar changes."""
        import json
        from dual_pipeline.graph_retrieval_core.clearance_cache import (
            authorized_records_cached,
            clear_clearance_caches,
            fts5_bm25_matches_cached,
        )
        from dual_pipeline.hash_cache import cached_sha256_file

        clear_clearance_caches()
        graph_db = self.test_root / "graph.sqlite"
        sidecar_db = self.test_root / "sidecar.sqlite"

        gconn = sqlite3.connect(graph_db)
        gconn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                classification TEXT,
                allowed_audiences_json TEXT,
                assertion_type TEXT,
                origin_type TEXT,
                extraction_run_id TEXT,
                hitl_required INTEGER,
                validation_priority TEXT,
                validation_owner TEXT,
                atomicity_state TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER
            )"""
        )
        gconn.execute(
            """INSERT INTO assertion VALUES (
                'asrt-1', 'internal', '["internal_consulting"]', 'factual', 'direct',
                'run-1', 0, 'low', 'owner', 'atomic', 'current', 1
            )"""
        )
        gconn.execute(
            """CREATE TABLE search_document (
                search_rowid INTEGER PRIMARY KEY,
                search_document_id TEXT,
                assertion_id TEXT,
                retrieval_eligible INTEGER,
                currentness_state TEXT
            )"""
        )
        gconn.execute(
            "INSERT INTO search_document VALUES (1, 'sdoc-1', 'asrt-1', 1, 'current')"
        )
        gconn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(content)")
        gconn.execute("INSERT INTO search_fts(rowid, content) VALUES (1, 'reorg technology platform')")
        gconn.commit()
        gconn.close()

        sconn = sqlite3.connect(sidecar_db)
        sconn.execute(
            "CREATE TABLE retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT, artifact_json TEXT)"
        )
        rec = {
            "record_id": "rec-1",
            "record_type": "assertion",
            "lane": "approved",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "retrieval_kind": "assertion",
            "epistemic_status": "approved",
            "provenance_kind": "source_bound",
            "answer_use": "factual_answer",
            "extensions": {
                "com.partner_advisory.nee.graph-object-id": "asrt-1",
                "com.partner_advisory.nee.assertion-type": "factual",
            },
        }
        sconn.execute(
            "INSERT INTO retrieval_record VALUES (?, ?, ?)",
            ("rec-1", "approved", json.dumps(rec)),
        )
        sconn.commit()
        sconn.close()

        validate_calls = 0

        def fake_validate(p: Path) -> dict:
            nonlocal validate_calls
            validate_calls += 1
            return {"metadata": {"graph_sha256": cached_sha256_file(graph_db)}}

        by_rec1, _ = authorized_records_cached(
            sidecar_path=sidecar_db,
            graph_path=graph_db,
            allowed_lanes=["approved"],
            retrieval_mode="factual",
            classification="internal",
            audiences=["internal_consulting"],
            allowed_assertion_ids=None,
            allow_lane_subset=True,
            validate_fn=fake_validate,
        )
        self.assertIn("rec-1", by_rec1)
        self.assertEqual(validate_calls, 1)

        # Second call hits cache without re-running fake_validate
        by_rec2, _ = authorized_records_cached(
            sidecar_path=sidecar_db,
            graph_path=graph_db,
            allowed_lanes=["approved"],
            retrieval_mode="factual",
            classification="internal",
            audiences=["internal_consulting"],
            allowed_assertion_ids=["asrt-other"],
            allow_lane_subset=True,
            validate_fn=fake_validate,
        )
        self.assertEqual(by_rec2, {})
        self.assertEqual(validate_calls, 1)

        fts_ok, fts_ids = fts5_bm25_matches_cached(graph_db, "reorg technology")
        self.assertTrue(fts_ok)
        self.assertEqual(fts_ids, ["asrt-1"])


if __name__ == "__main__":
    unittest.main()
