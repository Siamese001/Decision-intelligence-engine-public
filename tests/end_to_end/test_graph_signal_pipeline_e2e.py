#!/usr/bin/env python3
"""End-to-End Test Suite for GS-10 Graph Signal Pipeline Lifecycle.

Exercises the full lifecycle:
1. Baseline Generation Setup: Initial entities, sources, and assertions.
2. Ingestion Mutation with Mutex Supersession: Ingests fresh SSOT facts and verifies prior conflicting edges are superseded.
3. Graph Traversal Scoring: Verifies temporal decay and confidence weighting favor fresh facts.
4. Gate 30 Differential Gate: Validates pre/post non-degradation and zero mutex conflict entropy.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import connect_sqlite
from dual_pipeline.graph_retrieval_core.scoring import _edge_increment
from dual_pipeline.graph_signal_core import (
    evaluate_differential_signal,
    evaluate_graph_signal,
    supersede_conflicting_assertions,
)


@pytest.mark.e2e
class GraphSignalPipelineE2ETests(unittest.TestCase):
    """End-to-End test suite verifying complete GS-10 lifecycle."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.pre_graph = self.dir_path / "baseline_graph.sqlite"
        self.pre_sidecar = self.dir_path / "baseline_sidecar.sqlite"
        self.post_graph = self.dir_path / "active_graph.sqlite"
        self.post_sidecar = self.dir_path / "active_sidecar.sqlite"

        # Schema for baseline and active graphs
        for p in (self.pre_graph, self.post_graph):
            conn = connect_sqlite(p)
            conn.executescript(
                """
                CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, node_type TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL);
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    assertion_type TEXT,
                    confidence_score REAL,
                    origin_type TEXT,
                    validation_state TEXT,
                    currentness_state TEXT NOT NULL,
                    canonical_status TEXT NOT NULL,
                    superseded_at TEXT,
                    superseded_by_assertion_id TEXT,
                    retrieval_eligible INTEGER NOT NULL DEFAULT 1,
                    materiality TEXT DEFAULT 'medium',
                    recorded_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    primary_source_id TEXT NOT NULL
                );
                CREATE TABLE assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_relationship TEXT NOT NULL
                );
                CREATE TABLE search_document (
                    search_document_id TEXT PRIMARY KEY,
                    assertion_id TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    canonical_status TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT
                );
                """
            )
            conn.close()

        for s in (self.pre_sidecar, self.post_sidecar):
            conn_s = connect_sqlite(s)
            conn_s.executescript(
                """
                CREATE TABLE retrieval_record (
                    record_id TEXT PRIMARY KEY,
                    lane TEXT NOT NULL,
                    artifact_json TEXT NOT NULL
                );
                """
            )
            conn_s.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_pipeline_graph_signal_lifecycle_e2e(self) -> None:
        """Exercises complete intake, mutex invalidation, temporal scoring, and Gate 30 evaluation."""
        # Step 1: Establish baseline generation (June 2026 legacy lead)
        c_pre = connect_sqlite(self.pre_graph)
        c_pre.execute("INSERT INTO node VALUES ('person_alice', 'Alice', 'person', 1), ('workstream_ea', 'EA Pod', 'workstream', 1)")
        c_pre.execute("INSERT INTO source VALUES ('src_charter_june', 'June 2026 Charter')")
        c_pre.execute(
            """INSERT INTO assertion VALUES
               ('asrt_legacy_lead', 'person_alice', 'OWNS_OR_LEADS', NULL, 'workstream_ea', NULL, 'edge', 0.85, 'native', 'approved', 'current', 'canonical', NULL, NULL, 1, 'high', '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z', NULL, 'src_charter_june')"""
        )
        c_pre.execute("INSERT INTO assertion_evidence VALUES ('asrt_legacy_lead', 'ev_june_1', 'supports')")
        c_pre.execute("INSERT INTO search_document VALUES ('doc_legacy', 'asrt_legacy_lead', 'current', 'canonical', 1, '2026-06-15T00:00:00Z')")
        c_pre.commit()
        c_pre.close()

        c_pres = connect_sqlite(self.pre_sidecar)
        c_pres.execute("INSERT INTO retrieval_record VALUES ('rec_legacy', 'approved', '{\"currentness_state\":\"current\",\"canonical_status\":\"canonical\"}')")
        c_pres.commit()
        c_pres.close()

        # Step 2: Simulate post-mutation generation (Copy baseline, then ingest fresh Sept 2026 lead)
        # Populate post_graph initially with baseline data
        c_post = connect_sqlite(self.post_graph)
        c_post.execute("INSERT INTO node VALUES ('person_alice', 'Alice', 'person', 1), ('person_bob', 'Bob', 'person', 1), ('workstream_ea', 'EA Pod', 'workstream', 1)")
        c_post.execute("INSERT INTO source VALUES ('src_charter_june', 'June Charter'), ('src_charter_sept', 'Sept Charter')")
        c_post.execute(
            """INSERT INTO assertion VALUES
               ('asrt_legacy_lead', 'person_alice', 'OWNS_OR_LEADS', NULL, 'workstream_ea', NULL, 'edge', 0.85, 'native', 'approved', 'current', 'canonical', NULL, NULL, 1, 'high', '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z', NULL, 'src_charter_june'),
               ('asrt_live_lead', 'person_bob', 'OWNS_OR_LEADS', NULL, 'workstream_ea', NULL, 'edge', 0.95, 'native', 'approved', 'current', 'canonical', NULL, NULL, 1, 'high', '2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z', NULL, 'src_charter_sept')"""
        )
        c_post.execute("INSERT INTO assertion_evidence VALUES ('asrt_legacy_lead', 'ev_june_1', 'supports'), ('asrt_live_lead', 'ev_sept_1', 'supports')")
        c_post.execute("INSERT INTO search_document VALUES ('doc_legacy', 'asrt_legacy_lead', 'current', 'canonical', 1, '2026-06-15T00:00:00Z'), ('doc_live', 'asrt_live_lead', 'current', 'canonical', 1, '2026-09-25T00:00:00Z')")
        c_post.commit()

        # Execute Stage U0 automated mutex supersession
        superseded = supersede_conflicting_assertions(
            c_post,
            incoming_assertion_id="asrt_live_lead",
            subject_node_id="person_bob",
            predicate="OWNS_OR_LEADS",
            object_node_id="workstream_ea",
            now_iso="2026-09-25T12:00:00Z",
        )
        c_post.commit()
        self.assertEqual(superseded, ["asrt_legacy_lead"])

        # Verify database state after supersession
        legacy_row = c_post.execute(
            "SELECT currentness_state, superseded_by_assertion_id, retrieval_eligible FROM assertion WHERE assertion_id='asrt_legacy_lead'"
        ).fetchone()
        self.assertEqual(legacy_row[0], "historical")
        self.assertEqual(legacy_row[1], "asrt_live_lead")
        self.assertEqual(legacy_row[2], 0)

        legacy_doc = c_post.execute(
            "SELECT currentness_state, retrieval_eligible FROM search_document WHERE assertion_id='asrt_legacy_lead'"
        ).fetchone()
        self.assertEqual(legacy_doc[0], "historical")
        self.assertEqual(legacy_doc[1], 0)
        c_post.close()

        # Populate post_sidecar: only current/canonical assertions in approved lane
        c_posts = connect_sqlite(self.post_sidecar)
        c_posts.execute("INSERT INTO retrieval_record VALUES ('rec_live', 'approved', '{\"currentness_state\":\"current\",\"canonical_status\":\"canonical\"}')")
        c_posts.commit()
        c_posts.close()

        # Step 3: Verify Temporal Traversal Scoring favoring fresh evidence
        edge_fresh = {
            "relationship_family": "OWNS_OR_LEADS",
            "evidence_ids": ["ev_sept_1"],
            "materiality": "high",
            "confidence_score": 0.95,
            "recorded_at": "2026-09-25T00:00:00Z",
        }
        edge_stale = {
            "relationship_family": "OWNS_OR_LEADS",
            "evidence_ids": ["ev_june_1"],
            "materiality": "high",
            "confidence_score": 0.85,
            "recorded_at": "2026-06-15T00:00:00Z",
        }
        inc_fresh, topo_fresh = _edge_increment(edge_fresh, requested_relationships={"OWNS_OR_LEADS"}, destination_degree=1, hop=1)
        inc_stale, topo_stale = _edge_increment(edge_stale, requested_relationships={"OWNS_OR_LEADS"}, destination_degree=1, hop=1)
        self.assertGreater(inc_fresh, inc_stale)
        self.assertGreater(topo_fresh, topo_stale)

        # Step 4: Verify Gate 30 Pre/Post Differential Evaluation Passes
        pre_eval = evaluate_graph_signal(self.pre_graph, self.pre_sidecar)
        post_eval = evaluate_graph_signal(self.post_graph, self.post_sidecar)
        diff_eval = evaluate_differential_signal(pre_eval, post_eval)

        self.assertTrue(diff_eval["passed"])
        self.assertEqual(diff_eval["verdict"], "PASS")
        self.assertEqual(diff_eval["post_metrics"]["mutex_conflict_entropy_h"], 0)
        self.assertEqual(diff_eval["post_metrics"]["approved_lane_purity_pi"], 1.0)
        self.assertEqual(diff_eval["post_metrics"]["orphan_edge_ratio"], 0.0)
        self.assertGreaterEqual(diff_eval["deltas"]["delta_composite_score_q"], 0.0)


if __name__ == "__main__":
    unittest.main()
