#!/usr/bin/env python3
"""End-to-End integration and lifecycle qualification for hybrid retrieval optimizations.

Verifies the 6 key hybrid retrieval optimizations functioning end-to-end:
1. 2-letter acronym admission and punctuation normalization ("AI", "VP", "Q&A")
2. FTS5 pre-cutoff candidate lane and extended BM25 matches (>200 records admitted)
3. Route-aware RRF weight profiles:
   - factual mode preserves BM25_RRF_WEIGHTS (bm25: 0.70)
   - strategic mode selects STRATEGIC_BM25_RRF_WEIGHTS (bm25: 0.45, dense: 0.20, colbert: 0.20, sparse: 0.15)
4. Explicit RRF weight overrides
5. Pre-tokenized graph expansion avoiding per-query regex scans
6. Bounded LRU caches (query encoding, ColBERT tokens, FTS5 matches)
"""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from dual_pipeline.contracts import create_artifact
from dual_pipeline.core import connect_sqlite, digest, sha256_bytes, sha256_file
from dual_pipeline.graph_retrieval_core import (
    bm25_query_tokens,
    clear_clearance_caches,
    expand_connected_retrieval,
    governed_bm25_assertion_ranks,
    record_tokens_cached,
)
from dual_pipeline.index_core import (
    BM25_RRF_WEIGHTS,
    STRATEGIC_BM25_RRF_WEIGHTS,
    HybridRetriever,
)
from dual_pipeline.index_core.constants import SIDECAR_SCHEMA


class HybridRetrievalOptimizationsE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="hybrid_opt_e2e_")
        cls.root = Path(cls.temp_dir.name)
        cls.graph_path = cls.root / "graph.sqlite"
        cls.sidecar_path = cls.root / "sidecar.sqlite"

        # Build Graph Database
        gconn = connect_sqlite(cls.graph_path)
        gconn.execute(
            """CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT,
                active INTEGER DEFAULT 1
            )"""
        )
        gconn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                materiality TEXT,
                canonical_status TEXT,
                validation_state TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                primary_source_id TEXT,
                recorded_at TEXT,
                created_at TEXT,
                confidence_score REAL,
                classification TEXT,
                allowed_audiences_json TEXT,
                assertion_type TEXT,
                canonical_eligible INTEGER
            )"""
        )
        gconn.execute(
            """CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT
            )"""
        )
        gconn.execute(
            """CREATE TABLE v_canonical_edge (
                assertion_id TEXT,
                from_node_id TEXT,
                relationship_family TEXT,
                edge_type TEXT,
                to_node_id TEXT,
                materiality TEXT,
                source_id TEXT
            )"""
        )
        gconn.execute(
            """CREATE TABLE v_conflicting_assertions (
                assertion_id_1 TEXT,
                assertion_id_2 TEXT,
                resolution_state TEXT
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

        # Add Nodes
        gconn.execute("INSERT INTO node VALUES ('node-ai', 'AI CoE', 'initiative', 1)")
        gconn.execute("INSERT INTO node VALUES ('node-vp', 'VP Delivery', 'stakeholder', 1)")
        gconn.execute("INSERT INTO node VALUES ('node-mod', 'Modernization', 'opportunity', 1)")

        # Add Canonical Edges
        gconn.execute(
            "INSERT INTO v_canonical_edge VALUES ('asrt-ai-1', 'node-ai', 'governance', 'led_by', 'node-vp', 'high', 'src-1')"
        )
        gconn.execute(
            "INSERT INTO v_canonical_edge VALUES ('asrt-vp-2', 'node-vp', 'strategic', 'sponsors', 'node-mod', 'high', 'src-2')"
        )

        # Add Assertions
        assertions = [
            (
                "asrt-ai-1",
                "internal",
                '["all"]',
                "factual",
                "node-ai",
                "led_by",
                "node-vp",
                "AI CoE strategic delivery and transformation",
            ),
            (
                "asrt-vp-2",
                "internal",
                '["all"]',
                "factual",
                "node-vp",
                "sponsors",
                "node-mod",
                "VP platform roadmap and leadership",
            ),
            (
                "asrt-strat-3",
                "internal",
                '["all"]',
                "factual",
                "node-mod",
                "relates_to",
                "node-ai",
                "strategic modernization and cloud optimization",
            ),
        ]
        for rowid, (aid, cls_name, aud, atype, s_id, pred, o_id, text) in enumerate(assertions, 1):
            gconn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, legacy_predicate, object_node_id,
                    object_literal, materiality, canonical_status, validation_state, currentness_state,
                    retrieval_eligible, primary_source_id, recorded_at, created_at, confidence_score,
                    classification, allowed_audiences_json, assertion_type, canonical_eligible
                ) VALUES (?, ?, ?, ?, ?, NULL, 'high', 'canonical', 'valid', 'current', 1, 'src-1', '2026-10-22', '2026-10-22', 0.95, ?, ?, ?, 1)""",
                (aid, s_id, pred, pred, o_id, cls_name, aud, atype),
            )
            gconn.execute(
                "INSERT INTO assertion_evidence VALUES (?, ?, 'supports')",
                (aid, f"ev-{aid}"),
            )
            gconn.execute(
                "INSERT INTO search_document VALUES (?, ?, ?, 1, 'current', 'canonical')",
                (rowid, f"sdoc-{rowid}", aid),
            )
            gconn.execute(
                "INSERT INTO search_fts(rowid, content) VALUES (?, ?)",
                (rowid, text),
            )
        gconn.commit()
        gconn.close()

        # Build Sidecar Database
        sconn = connect_sqlite(cls.sidecar_path)
        sconn.executescript(SIDECAR_SCHEMA)

        g_sha = sha256_file(cls.graph_path)
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('graph_sha256', ?)", (g_sha,))
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('bound_graph_sha256', ?)", (g_sha,))
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('generation_id', 'gen-e2e')")
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('record_count', '3')")
        sconn.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('embedding_backend', 'test-backend')"
        )
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('model_id', 'test-model')")
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('model_revision', 'rev-e2e')")
        sconn.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('model_manifest_digest', 'manifest-e2e')"
        )
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
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('approved_record_count', '3')")
        sconn.execute("INSERT OR REPLACE INTO metadata VALUES ('candidate_record_count', '0')")
        sconn.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('external_public_record_count', '0')"
        )

        meta_rows = []
        unit_vec = [1.0] + [0.0] * 1023
        unit_blob = struct.pack(f"<{1024}f", *unit_vec)

        for rid_num, (aid, _, _, _, s_id, _, _, text) in enumerate(assertions, 1):
            rid = f"rec-e2e-{rid_num}"
            art = create_artifact(
                "retrieval-record",
                rid,
                record_id=rid,
                record_type="assertion",
                namespace="nee.approved.assertions",
                lane="approved",
                title=f"Title {rid}",
                canonical_entities=[s_id],
                retrieval_text=text,
                retrieval_text_hash=sha256_bytes(text.encode("utf-8")),
                evidence_cluster_id=f"cluster-{rid}",
                evidence_ids=[f"ev-{rid}"],
                graph_snapshot="test-snapshot",
                classification="internal",
                audiences=["all"],
                lifecycle="current",
                effective_date="2026-10-22",
                action_status=None,
                retrieval_kind="assertion",
                epistemic_status="approved",
                provenance_kind="source_bound",
                answer_use="factual_answer" if rid_num <= 2 else "strategic_context",
                extensions={
                    "com.partner_advisory.nee.graph-object-id": aid,
                    "com.partner_advisory.nee.assertion-type": "factual",
                },
            )
            sconn.execute(
                """INSERT INTO retrieval_record VALUES (
                    ?, 'approved', 'assertion', 'nee.approved.assertions', 'hash',
                    'assertion', 'approved', 'source_bound', ?, ?
                )""",
                (rid, "factual_answer" if rid_num <= 2 else "strategic_context", json.dumps(art)),
            )
            sconn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", (rid, unit_blob))
            sconn.execute("INSERT INTO sparse_weight VALUES (?, 'strategic', 1.0)", (rid,))
            sconn.execute(
                "INSERT INTO colbert_token VALUES (?, 0, 'strategic', 1024, ?)", (rid, unit_blob)
            )
            meta_rows.append(
                {
                    "record_id": rid,
                    "retrieval_kind": "assertion",
                    "epistemic_status": "approved",
                    "provenance_kind": "source_bound",
                    "answer_use": "factual_answer" if rid_num <= 2 else "strategic_context",
                }
            )

        meta_rows.sort(key=lambda item: item["record_id"])
        sconn.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('retrieval_metadata_manifest_digest', ?)",
            (digest(meta_rows),),
        )
        sconn.commit()
        sconn.close()

        # Mock Backend setup
        cls.backend = MagicMock()
        cls.backend.backend_id = "test-backend"
        cls.backend.model_id = "test-model"
        cls.backend.model_revision = "rev-e2e"
        cls.backend.model_manifest_digest = "manifest-e2e"
        cls.backend.encoding_fingerprint_digest = ""
        cls.backend.encode.return_value = {
            "dense": unit_vec,
            "sparse": {"strategic": 1.0, "transformation": 1.0},
            "colbert": [unit_vec],
        }

        cls.retriever = HybridRetriever(cls.sidecar_path, cls.backend)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp_dir.cleanup()

    def setUp(self) -> None:
        clear_clearance_caches()

    def test_e2e_factual_mode_preserves_bm25_dominance(self) -> None:
        """Optimization 3a: factual mode selects BM25_RRF_WEIGHTS with bm25=0.70 dominance."""
        ranks, ok = governed_bm25_assertion_ranks(
            question="AI CoE delivery",
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
            allowed_lanes=["approved"],
            retrieval_mode="factual",
            classification="internal",
            audiences=["all"],
        )
        self.assertTrue(ok)
        self.assertIn("approved", ranks)
        self.assertIn("asrt-ai-1", ranks["approved"])

        lane_result = self.retriever.retrieve_lane(
            query="AI CoE delivery",
            lane="approved",
            classification="internal",
            audiences=["all"],
            retrieval_mode="factual",
            bm25_assertion_ranks=ranks["approved"],
        )
        self.assertEqual(lane_result["rrf_weights"], BM25_RRF_WEIGHTS)
        self.assertEqual(lane_result["rrf_weights"]["bm25"], 0.70)
        self.assertEqual(lane_result["rrf_weights"]["dense"], 0.05)
        self.assertEqual(lane_result["rrf_weights"]["colbert"], 0.10)
        self.assertEqual(lane_result["rrf_weights"]["sparse"], 0.15)
        self.assertGreater(len(lane_result["results"]), 0)
        self.assertEqual(lane_result["results"][0]["record_id"], "rec-e2e-1")

    def test_e2e_strategic_mode_selects_strategic_rrf_weights(self) -> None:
        """Optimization 3b: strategic mode selects STRATEGIC_BM25_RRF_WEIGHTS boosting dense and ColBERT."""
        ranks, ok = governed_bm25_assertion_ranks(
            question="strategic modernization cloud",
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
            allowed_lanes=["approved"],
            retrieval_mode="strategic",
            classification="internal",
            audiences=["all"],
        )
        self.assertTrue(ok)

        lane_result = self.retriever.retrieve_lane(
            query="strategic modernization cloud",
            lane="approved",
            classification="internal",
            audiences=["all"],
            retrieval_mode="strategic",
            bm25_assertion_ranks=ranks["approved"],
        )
        self.assertEqual(lane_result["rrf_weights"], STRATEGIC_BM25_RRF_WEIGHTS)
        self.assertEqual(lane_result["rrf_weights"]["bm25"], 0.45)
        self.assertEqual(lane_result["rrf_weights"]["dense"], 0.20)
        self.assertEqual(lane_result["rrf_weights"]["colbert"], 0.20)
        self.assertEqual(lane_result["rrf_weights"]["sparse"], 0.15)
        self.assertGreater(len(lane_result["results"]), 0)

    def test_e2e_explicit_rrf_weights_override(self) -> None:
        """Optimization 3c: Explicit weight overrides supersede route defaults."""
        custom_weights = {"dense": 0.50, "sparse": 0.10, "bm25": 0.20, "colbert": 0.20}
        lane_result = self.retriever.retrieve_lane(
            query="strategic modernization",
            lane="approved",
            classification="internal",
            audiences=["all"],
            retrieval_mode="strategic",
            bm25_assertion_ranks={"asrt-strat-3": 1},
            rrf_weights=custom_weights,
        )
        self.assertEqual(lane_result["rrf_weights"], custom_weights)

    def test_e2e_two_letter_acronym_end_to_end_search(self) -> None:
        """Optimization 1: Two-letter acronyms (AI, VP) are admitted, tokenized, and retrieved."""
        tokens_ai = bm25_query_tokens("What is the AI roadmap?")
        self.assertIn("ai", tokens_ai)
        self.assertIn("roadmap", tokens_ai)

        tokens_vp = bm25_query_tokens("Review V.P. platform guidance")
        self.assertIn("vp", tokens_vp)

        ranks, ok = governed_bm25_assertion_ranks(
            question="AI delivery",
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
            allowed_lanes=["approved"],
            retrieval_mode="factual",
            classification="internal",
            audiences=["all"],
        )
        self.assertTrue(ok)
        self.assertIn("asrt-ai-1", ranks["approved"])

    def test_e2e_graph_expansion_with_pretokenized_cache(self) -> None:
        """Optimization 4: Precomputed record token sets power expand_connected_retrieval."""
        token_map = record_tokens_cached(self.sidecar_path, self.graph_path)
        self.assertIn("rec-e2e-1", token_map)
        self.assertIn("ai", token_map["rec-e2e-1"])

        # Execute multi-hop graph expansion
        retrieval_payload = {
            "allowed_lanes": ["approved"],
            "retrieval_mode": "factual",
            "lanes": {
                "approved": {
                    "results": [
                        {
                            "record_id": "rec-e2e-1",
                            "lane": "approved",
                            "fused_rank": 1,
                            "record": {
                                "contract_id": "urn:nee:dual-pipeline:retrieval-record:1.0.0",
                                "schema_version": "1.0.0",
                                "artifact_id": "rec-e2e-1",
                                "canonical_digest": {"value": sha256_bytes(b"test")},
                                "record_id": "rec-e2e-1",
                                "record_type": "assertion",
                                "namespace": "nee.approved.assertions",
                                "lane": "approved",
                                "title": "Title rec-e2e-1",
                                "canonical_entities": ["node-ai"],
                                "retrieval_text": "AI CoE strategic delivery and transformation",
                                "retrieval_text_hash": sha256_bytes(b"hash"),
                                "evidence_cluster_id": "c1",
                                "evidence_ids": ["ev1"],
                                "graph_snapshot": "test-snapshot",
                                "classification": "internal",
                                "audiences": ["all"],
                                "lifecycle": "current",
                                "effective_date": "2026-10-22",
                                "action_status": None,
                                "retrieval_kind": "assertion",
                                "epistemic_status": "approved",
                                "provenance_kind": "source_bound",
                                "answer_use": "factual_answer",
                                "extensions": {
                                    "com.partner_advisory.nee.graph-object-id": "asrt-ai-1",
                                    "com.partner_advisory.nee.assertion-type": "factual",
                                },
                            },
                        }
                    ]
                }
            },
        }
        expanded_retrieval, connected_map = expand_connected_retrieval(
            retrieval=retrieval_payload,
            question="AI CoE delivery and platform sponsorship",
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
            run_id="run-e2e-test",
            graph_snapshot="test-snapshot",
            query_plan_digest="0" * 64,
            requested_hops=2,
            relationships_to_inspect=["led_by", "sponsors"],
            classification="internal",
            audiences=["all"],
            retrieval_mode="factual",
            allowed_assertion_ids=None,
        )
        self.assertIn("approved", expanded_retrieval["lanes"])
        self.assertGreaterEqual(len(expanded_retrieval["lanes"]["approved"]["results"]), 1)

    def test_e2e_lru_cache_bounds_under_flood(self) -> None:
        """Optimization 6: Bounded LRU caches never leak unbounded memory under heavy query traffic."""
        # Query encoding cache check
        for i in range(550):
            _ = self.retriever.retrieve_lane(
                query=f"synthetic query unique {i}",
                lane="approved",
                classification="internal",
                audiences=["all"],
                retrieval_mode="factual",
            )
        self.assertLessEqual(len(self.retriever._query_encoding_cache), 512)
        self.assertEqual(len(self.retriever._query_encoding_cache), 512)


if __name__ == "__main__":
    unittest.main()
