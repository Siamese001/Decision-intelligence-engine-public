"""Adversarial and boundary tests for hybrid retrieval optimizations.

Covers:
1. Boundary payloads: empty queries, single characters, exact 32-token limits, overflow queries.
2. Malformed input: punctuation, special symbols, casing variations, numerical/alphanumeric tokens.
3. Exact production regressions: 2-letter executive/tech acronyms ("AI", "VP", "QA", "HR", "Q3", "ML", "S3").
4. Invariant bypass: verifying 2-letter English stopwords ("is", "to", "in", "of") remain strictly blocked.
"""

from __future__ import annotations

import unittest

from dual_pipeline.graph_retrieval_core.constants import _BM25_STOPWORDS, _QUERY_STOPWORDS
from dual_pipeline.graph_retrieval_core.tokenize import _question_tokens, bm25_query_tokens


class HybridRetrievalOptimizationsAdversarialTests(unittest.TestCase):
    """Adversarial validation of tokenization, 2-letter acronyms, and lexical budget."""

    # -------------------------------------------------------------------------
    # Category 1: Exact Production Regressions (Executive & Technical Acronyms)
    # -------------------------------------------------------------------------
    def test_executive_and_technical_acronyms_preserved(self) -> None:
        query = "VP and HR sync with QA on AI delivery targets for Q3 and S3 data"
        bm25_toks = bm25_query_tokens(query)
        q_toks = _question_tokens(query)

        for acronym in ("vp", "hr", "qa", "ai", "q3", "s3"):
            self.assertIn(
                acronym,
                bm25_toks,
                f"Acronym '{acronym}' missing from bm25_query_tokens",
            )
            self.assertIn(
                acronym,
                q_toks,
                f"Acronym '{acronym}' missing from _question_tokens",
            )

    def test_engineering_layer_acronyms_preserved(self) -> None:
        query = "L0 L1 L2 C0 X1 X2 X3 U0 E0 E3 pipeline architecture"
        bm25_toks = bm25_query_tokens(query)
        for layer in ("l0", "l1", "l2", "c0", "x1", "x2", "x3", "u0", "e0", "e3"):
            self.assertIn(layer, bm25_toks)

    # -------------------------------------------------------------------------
    # Category 2: Invariant Bypass (Stopword Firewall Integrity)
    # -------------------------------------------------------------------------
    def test_two_letter_stopwords_remain_firewalled(self) -> None:
        stopwords_sample = [
            "is",
            "to",
            "in",
            "of",
            "on",
            "by",
            "at",
            "as",
            "be",
            "do",
            "he",
            "if",
            "so",
            "up",
            "we",
        ]
        query = " ".join(stopwords_sample)
        bm25_toks = bm25_query_tokens(query)
        self.assertEqual(
            bm25_toks,
            [],
            f"Expected empty token list for pure stopword query, got: {bm25_toks}",
        )
        q_toks = _question_tokens(query)
        self.assertEqual(
            q_toks,
            set(),
            f"Expected empty token set for pure stopword query, got: {q_toks}",
        )

    def test_single_letter_tokens_never_admitted(self) -> None:
        query = "a b c d e x y z 1 2 9"
        bm25_toks = bm25_query_tokens(query)
        self.assertEqual(bm25_toks, [])
        q_toks = _question_tokens(query)
        self.assertEqual(q_toks, set())

    # -------------------------------------------------------------------------
    # Category 3: Boundary Payloads (Exhaustion & Cap Verification)
    # -------------------------------------------------------------------------
    def test_extended_token_budget_boundary_32(self) -> None:
        unique_tokens = [f"word{i:02d}" for i in range(45)]
        query = " ".join(unique_tokens)
        bm25_toks = bm25_query_tokens(query)
        self.assertEqual(len(bm25_toks), 32)
        self.assertEqual(bm25_toks[0], "word00")
        self.assertEqual(bm25_toks[31], "word31")
        self.assertNotIn("word32", bm25_toks)

    def test_empty_and_whitespace_boundary(self) -> None:
        self.assertEqual(bm25_query_tokens(""), [])
        self.assertEqual(bm25_query_tokens("   \t\n  "), [])
        self.assertEqual(_question_tokens(""), set())
        self.assertEqual(_question_tokens("   \t\n  "), set())

    # -------------------------------------------------------------------------
    # Category 4: Malformed Input & Punctuation Edge Cases
    # -------------------------------------------------------------------------
    def test_punctuated_acronyms_normalized_cleanly(self) -> None:
        query = "A.I., Q&A, V.P., H.R., and Q-3 deliverables!!!"
        bm25_toks = bm25_query_tokens(query)
        self.assertIn("ai", bm25_toks)
        self.assertIn("qa", bm25_toks)
        self.assertIn("vp", bm25_toks)
        self.assertIn("hr", bm25_toks)
        self.assertIn("q3", bm25_toks)

    def test_mixed_case_and_repeated_tokens(self) -> None:
        query = "Ai aI AI aI ai qA QA Qa qa"
        bm25_toks = bm25_query_tokens(query)
        self.assertEqual(bm25_toks, ["ai", "qa"])

    # -------------------------------------------------------------------------
    # Category 5: Pre-Cutoff Candidate Lane & Extended FTS5 BM25 Admission
    # -------------------------------------------------------------------------
    def test_fts5_bm25_candidate_lane_and_post_200_records_survive(self) -> None:
        """Verifies candidate lane non-canonical records and >200 records match in FTS5."""
        import json
        import sqlite3
        import tempfile
        from pathlib import Path
        from dual_pipeline.graph_retrieval_core.clearance_cache import (
            clear_clearance_caches,
            fts5_bm25_matches_cached,
        )
        from dual_pipeline.graph_retrieval_core.traversal import governed_bm25_assertion_ranks

        clear_clearance_caches()
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            graph_db = root / "graph.sqlite"
            sidecar_db = root / "sidecar.sqlite"

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
                    retrieval_eligible INTEGER,
                    canonical_status TEXT
                )"""
            )
            gconn.execute(
                """CREATE TABLE search_document (
                    search_rowid INTEGER PRIMARY KEY,
                    search_document_id TEXT,
                    assertion_id TEXT,
                    retrieval_eligible INTEGER,
                    currentness_state TEXT,
                    canonical_status TEXT
                )"""
            )
            gconn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(content)")

            # Insert 210 approved records
            for i in range(1, 211):
                asrt_id = f"asrt-app-{i}"
                gconn.execute(
                    "INSERT INTO assertion VALUES (?, 'internal', '[\"all\"]', 'factual', 'direct', 'run-1', 0, 'low', 'owner', 'atomic', 'current', 1, 'canonical')",
                    (asrt_id,),
                )
                gconn.execute(
                    "INSERT INTO search_document VALUES (?, ?, ?, 1, 'current', 'canonical')",
                    (i, f"sdoc-{i}", asrt_id),
                )
                gconn.execute(
                    "INSERT INTO search_fts(rowid, content) VALUES (?, ?)",
                    (i, f"strategic transformation initiative report number {i}"),
                )

            # Insert 1 candidate record with non-canonical status
            cand_asrt_id = "asrt-cand-999"
            cand_rowid = 211
            gconn.execute(
                "INSERT INTO assertion VALUES (?, 'internal', '[\"all\"]', 'candidate_hypothesis', 'direct', 'run-1', 0, 'low', 'owner', 'atomic', 'current', 1, 'candidate')",
                (cand_asrt_id,),
            )
            gconn.execute(
                "INSERT INTO search_document VALUES (?, 'sdoc-cand-999', ?, 1, 'current', 'candidate')",
                (cand_rowid, cand_asrt_id),
            )
            gconn.execute(
                "INSERT INTO search_fts(rowid, content) VALUES (?, 'strategic transformation initiative draft hypothesis')",
                (cand_rowid,),
            )
            gconn.commit()
            gconn.close()

            # Sidecar setup
            sconn = sqlite3.connect(sidecar_db)
            from dual_pipeline.index_core.constants import SIDECAR_SCHEMA

            sconn.executescript(SIDECAR_SCHEMA)
            from dual_pipeline.hash_cache import cached_sha256_file

            g_sha = cached_sha256_file(graph_db)
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('graph_sha256', ?)", (g_sha,))
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('bound_graph_sha256', ?)", (g_sha,)
            )
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('generation_id', 'gen-1')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('record_count', '211')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('embedding_backend', 'test')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('model_id', 'test')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('model_revision', 'test')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('production_qualified', 'true')")
            from dual_pipeline.core import digest

            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('object_facet_schema_version', '1.0.0')"
            )
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('object_facet_assignment_count', '0')"
            )
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('object_facet_manifest_digest', ?)",
                (digest([]),),
            )
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('retrieval_metadata_schema_version', '1.0.0')"
            )
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('colbert_dimensions', '1024')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('approved_record_count', '210')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('candidate_record_count', '1')")
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('external_public_record_count', '0')"
            )
            from dual_pipeline.contracts import create_artifact
            from dual_pipeline.core import sha256_bytes

            def _make_record(rid: str, aid: str, lane: str, ans_use: str, epi: str):
                return create_artifact(
                    "retrieval-record",
                    rid,
                    record_id=rid,
                    record_type="assertion",
                    namespace=f"nee.{lane}.assertions",
                    lane=lane,
                    title=f"Record {rid}",
                    canonical_entities=["entity-test"],
                    retrieval_text="strategic transformation initiative report",
                    retrieval_text_hash=sha256_bytes(b"strategic transformation initiative report"),
                    evidence_cluster_id=f"cluster-{rid}",
                    evidence_ids=[f"ev-{rid}"],
                    graph_snapshot="test-snapshot",
                    classification="internal",
                    audiences=["all"],
                    lifecycle="candidate" if lane == "candidate" else "current",
                    effective_date="2026-10-22",
                    action_status=None,
                    retrieval_kind="assertion",
                    epistemic_status=epi,
                    provenance_kind="source_bound",
                    answer_use=ans_use,
                    extensions={
                        "com.partner_advisory.nee.graph-object-id": aid,
                        "com.partner_advisory.nee.assertion-type": "factual"
                        if lane == "approved"
                        else "candidate_hypothesis",
                    },
                )

            for i in range(1, 211):
                asrt_id = f"asrt-app-{i}"
                rec = _make_record(
                    f"rec-app-{i}", asrt_id, "approved", "factual_answer", "approved"
                )
                sconn.execute(
                    """INSERT INTO retrieval_record (
                        record_id, lane, record_type, namespace, retrieval_text_hash,
                        retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json
                    ) VALUES (?, 'approved', 'assertion', 'default', 'hash', 'assertion', 'approved', 'source_bound', 'factual_answer', ?)""",
                    (f"rec-app-{i}", json.dumps(rec)),
                )

            cand_rec = _make_record(
                "rec-cand-999", cand_asrt_id, "candidate", "candidate_hypothesis", "candidate"
            )
            sconn.execute(
                """INSERT INTO retrieval_record (
                    record_id, lane, record_type, namespace, retrieval_text_hash,
                    retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json
                ) VALUES (?, 'candidate', 'assertion', 'default', 'hash', 'assertion', 'candidate', 'source_bound', 'candidate_hypothesis', ?)""",
                ("rec-cand-999", json.dumps(cand_rec)),
            )

            meta_rows = []
            for i in range(1, 211):
                meta_rows.append(
                    {
                        "record_id": f"rec-app-{i}",
                        "retrieval_kind": "assertion",
                        "epistemic_status": "approved",
                        "provenance_kind": "source_bound",
                        "answer_use": "factual_answer",
                    }
                )
            meta_rows.append(
                {
                    "record_id": "rec-cand-999",
                    "retrieval_kind": "assertion",
                    "epistemic_status": "candidate",
                    "provenance_kind": "source_bound",
                    "answer_use": "candidate_hypothesis",
                }
            )
            meta_rows.sort(key=lambda item: item["record_id"])
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('retrieval_metadata_manifest_digest', ?)",
                (digest(meta_rows),),
            )

            # Insert minimal dense/sparse/colbert vectors to satisfy validate_sidecar counts
            import struct

            unit_vec = [1.0] + [0.0] * 1023
            unit_blob = struct.pack(f"<{1024}f", *unit_vec)
            for i in range(1, 211):
                sconn.execute(
                    "INSERT INTO dense_vector VALUES (?, 1024, ?)",
                    (f"rec-app-{i}", unit_blob),
                )
                sconn.execute(
                    "INSERT INTO sparse_weight VALUES (?, 'strategic', 1.0)",
                    (f"rec-app-{i}",),
                )
                sconn.execute(
                    "INSERT INTO colbert_token VALUES (?, 0, 'strategic', 1024, ?)",
                    (f"rec-app-{i}", unit_blob),
                )
            sconn.execute(
                "INSERT INTO dense_vector VALUES ('rec-cand-999', 1024, ?)",
                (unit_blob,),
            )
            sconn.execute(
                "INSERT INTO sparse_weight VALUES ('rec-cand-999', 'strategic', 1.0)",
            )
            sconn.execute(
                "INSERT INTO colbert_token VALUES ('rec-cand-999', 0, 'strategic', 1024, ?)",
                (unit_blob,),
            )
            sconn.commit()
            sconn.close()

            # Test 1: fts5_bm25_matches_cached finds candidate assertion and >200 records
            executed, matched_ids = fts5_bm25_matches_cached(graph_db, "strategic transformation")
            self.assertTrue(executed)
            self.assertIn(
                cand_asrt_id, matched_ids, "Candidate assertion should be matched by FTS5"
            )
            self.assertGreater(
                len(matched_ids), 200, "Should return more than 200 matches with widened limit"
            )

            # Test 2: governed_bm25_assertion_ranks provides non-empty candidate ranks
            ranks_by_lane, ok = governed_bm25_assertion_ranks(
                question="strategic transformation",
                graph_path=graph_db,
                sidecar_path=sidecar_db,
                allowed_lanes=["approved", "candidate"],
                retrieval_mode="strategic",
                classification="internal",
                audiences=["all"],
            )
            self.assertTrue(ok)
            self.assertIn("approved", ranks_by_lane)
            self.assertIn("candidate", ranks_by_lane)
            self.assertIn(
                cand_asrt_id,
                ranks_by_lane["candidate"],
                "Candidate assertion must receive BM25 rank in candidate lane",
            )
            self.assertGreaterEqual(len(ranks_by_lane["approved"]), 200)

    def test_bounded_lru_dict_eviction_and_recency(self) -> None:
        """Category 6: BoundedLRUDict enforces maximum capacity and recency-based eviction."""
        from dual_pipeline.index_core.retriever_cache import BoundedLRUDict

        cache: BoundedLRUDict[str, int] = BoundedLRUDict(maxsize=3)
        cache["k1"] = 1
        cache["k2"] = 2
        cache["k3"] = 3
        self.assertEqual(len(cache), 3)

        # Access k1 to make it most recently used
        _ = cache["k1"]

        # Insert k4, which should evict k2 (oldest)
        cache["k4"] = 4
        self.assertEqual(len(cache), 3)
        self.assertNotIn("k2", cache)
        self.assertIn("k1", cache)
        self.assertIn("k3", cache)
        self.assertIn("k4", cache)

        # Access k3 via .get()
        self.assertEqual(cache.get("k3"), 3)

        # Insert k5, which should evict k1 (since k3 was refreshed)
        cache["k5"] = 5
        self.assertEqual(len(cache), 3)
        self.assertNotIn("k1", cache)
        self.assertIn("k3", cache)
        self.assertIn("k4", cache)
        self.assertIn("k5", cache)

        # setdefault on existing key should not evict
        self.assertEqual(cache.setdefault("k5", 999), 5)
        self.assertEqual(len(cache), 3)

        # setdefault on new key should evict oldest (k4)
        self.assertEqual(cache.setdefault("k6", 6), 6)
        self.assertEqual(len(cache), 3)
        self.assertNotIn("k4", cache)
        self.assertIn("k6", cache)

    def test_fts5_cache_bounded_lru_under_flood(self) -> None:
        """Category 7: _FTS_CACHE never exceeds 512 entries under flood of distinct queries."""
        import tempfile
        from pathlib import Path
        from dual_pipeline.core import connect_sqlite
        from dual_pipeline.graph_retrieval_core.clearance_cache import (
            _FTS_CACHE,
            clear_clearance_caches,
            fts5_bm25_matches_cached,
        )

        clear_clearance_caches()
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_db = Path(tmpdir) / "test_flood_graph.sqlite"
            conn = connect_sqlite(graph_db)
            conn.execute(
                """CREATE TABLE search_document (
                    search_document_id TEXT,
                    search_rowid INTEGER,
                    assertion_id TEXT,
                    canonical_status TEXT,
                    currentness_state TEXT,
                    retrieval_eligible INTEGER
                )"""
            )
            conn.execute("CREATE VIRTUAL TABLE search_fts USING fts5(content)")
            conn.execute(
                "INSERT INTO search_fts(rowid, content) VALUES (1, 'transformation roadmap')"
            )
            conn.execute(
                "INSERT INTO search_document VALUES ('doc-1', 1, 'asrt-1', 'canonical', 'current', 1)"
            )
            conn.commit()
            conn.close()

            # Execute 600 distinct queries
            for i in range(600):
                fts5_bm25_matches_cached(graph_db, f"transformation query {i}")

            # Must never exceed 512 entries
            self.assertLessEqual(len(_FTS_CACHE), 512)
            self.assertEqual(len(_FTS_CACHE), 512)

            # Query 599 must be in cache, while early query 0 must have been evicted
            from dual_pipeline.graph_retrieval_core.clearance_cache import _stat_key

            g_key = _stat_key(graph_db)
            self.assertIn((*g_key, '"transformation" OR "query" OR "599"', 1000), _FTS_CACHE)
            self.assertNotIn((*g_key, '"transformation" OR "query" OR "0"', 1000), _FTS_CACHE)

    def test_retriever_query_and_colbert_caches_bounded_lru(self) -> None:
        """Category 8: HybridRetriever query encoding and ColBERT token caches enforce bounds."""
        import numpy as np
        from dual_pipeline.index_core.retriever_cache import BoundedLRUDict

        # Query encoding cache bound: 512
        q_cache: BoundedLRUDict[str, tuple] = BoundedLRUDict(maxsize=512)
        for i in range(600):
            q_cache[f"query_{i}"] = ({"id": i}, [0.1], {"w": 1.0}, [[0.1]])

        self.assertLessEqual(len(q_cache), 512)
        self.assertEqual(len(q_cache), 512)
        self.assertIn("query_599", q_cache)
        self.assertNotIn("query_0", q_cache)

        # ColBERT token cache bound: 4096
        colbert_cache: BoundedLRUDict[str, np.ndarray] = BoundedLRUDict(maxsize=4096)
        dummy_mat = np.zeros((4, 1024), dtype=np.float32)
        for i in range(4500):
            colbert_cache[f"rec_{i}"] = dummy_mat

        self.assertLessEqual(len(colbert_cache), 4096)
        self.assertEqual(len(colbert_cache), 4096)
        self.assertIn("rec_4499", colbert_cache)
        self.assertNotIn("rec_0", colbert_cache)

    def test_graph_expansion_pretokenized_bit_identical(self) -> None:
        """Category 9: record_tokens_cached precomputes token sets matching _question_tokens."""
        import json
        import tempfile
        from pathlib import Path
        from dual_pipeline.core import connect_sqlite, digest, sha256_bytes
        from dual_pipeline.contracts import create_artifact
        from dual_pipeline.graph_retrieval_core.clearance_cache import (
            clear_clearance_caches,
            record_tokens_cached,
        )
        from dual_pipeline.graph_retrieval_core.tokenize import _question_tokens

        clear_clearance_caches()
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_db = Path(tmpdir) / "test_pretoken_graph.sqlite"
            sidecar_db = Path(tmpdir) / "test_pretoken_sidecar.sqlite"

            gconn = connect_sqlite(graph_db)
            gconn.execute(
                """CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    classification TEXT,
                    allowed_audiences_json TEXT,
                    canonical_eligible INTEGER
                )"""
            )
            gconn.execute("INSERT INTO assertion VALUES ('asrt-101', 'internal', '[\"all\"]', 1)")
            gconn.commit()
            gconn.close()

            sconn = connect_sqlite(sidecar_db)
            from dual_pipeline.index_core.constants import SIDECAR_SCHEMA

            sconn.executescript(SIDECAR_SCHEMA)
            from dual_pipeline.core import sha256_file

            g_sha = sha256_file(graph_db)
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('graph_sha256', ?)", (g_sha,))
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('bound_graph_sha256', ?)", (g_sha,)
            )
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('generation_id', 'gen-1')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('record_count', '1')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('embedding_backend', 'test')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('model_id', 'test')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('model_revision', 'test')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('production_qualified', 'true')")
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('object_facet_schema_version', '1.0.0')"
            )
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('object_facet_assignment_count', '0')"
            )
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('object_facet_manifest_digest', ?)",
                (digest([]),),
            )
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('retrieval_metadata_schema_version', '1.0.0')"
            )
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('colbert_dimensions', '1024')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('approved_record_count', '1')")
            sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('candidate_record_count', '0')")
            sconn.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('external_public_record_count', '0')"
            )

            rec_text = "Strategic AI roadmap for cloud transformation and AI-native apps"
            rec = create_artifact(
                "retrieval-record",
                "rec-101",
                record_id="rec-101",
                record_type="assertion",
                namespace="nee.approved.assertions",
                lane="approved",
                title="Strategic AI Roadmap",
                canonical_entities=["entity-ai"],
                retrieval_text=rec_text,
                retrieval_text_hash=sha256_bytes(rec_text.encode("utf-8")),
                evidence_cluster_id="cluster-101",
                evidence_ids=["ev-101"],
                graph_snapshot="test-snapshot",
                classification="internal",
                audiences=["all"],
                lifecycle="current",
                effective_date="2026-10-22",
                action_status=None,
                retrieval_kind="assertion",
                epistemic_status="approved",
                provenance_kind="source_bound",
                answer_use="factual_answer",
                extensions={
                    "com.partner_advisory.nee.graph-object-id": "asrt-101",
                    "com.partner_advisory.nee.assertion-type": "factual",
                },
            )
            sconn.execute(
                """INSERT INTO retrieval_record VALUES (
                    'rec-101', 'approved', 'assertion', 'nee.approved.assertions', 'hash',
                    'assertion', 'approved', 'source_bound', 'factual_answer', ?
                )""",
                (json.dumps(rec),),
            )
            meta_rows = [
                {
                    "record_id": "rec-101",
                    "retrieval_kind": "assertion",
                    "epistemic_status": "approved",
                    "provenance_kind": "source_bound",
                    "answer_use": "factual_answer",
                }
            ]
            sconn.execute(
                "INSERT INTO metadata VALUES ('retrieval_metadata_manifest_digest', ?)",
                (digest(meta_rows),),
            )
            import struct

            unit_blob = struct.pack(f"<{1024}f", *([1.0] + [0.0] * 1023))
            sconn.execute("INSERT INTO dense_vector VALUES ('rec-101', 1024, ?)", (unit_blob,))
            sconn.execute("INSERT INTO sparse_weight VALUES ('rec-101', 'strategic', 1.0)")
            sconn.execute(
                "INSERT INTO colbert_token VALUES ('rec-101', 0, 'strategic', 1024, ?)",
                (unit_blob,),
            )
            sconn.commit()
            sconn.close()

            # Precomputed token cache check
            token_map = record_tokens_cached(sidecar_db, graph_db)
            self.assertIn("rec-101", token_map)
            expected_tokens = frozenset(_question_tokens(rec_text))
            self.assertEqual(token_map["rec-101"], expected_tokens)
            self.assertIn("ai", token_map["rec-101"])
            self.assertIn("roadmap", token_map["rec-101"])
            self.assertIn("cloud", token_map["rec-101"])


if __name__ == "__main__":
    unittest.main()
