"""Unit tests for semantic unit reachability audit and eligibility classification."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.index_core.eligibility import classify_retrieval_eligibility
from dual_pipeline.index_core.reachability_audit import (
    audit_semantic_units_and_reachability,
    compute_geometry_metrics,
    compute_margin_tie_rates,
    format_reachability_report,
)


class TestRetrievalEligibilityClassification(unittest.TestCase):
    def test_historical_exclusion(self):
        row = {"currentness_state": "historical", "canonical_status": "canonical", "validation_state": "approved"}
        eligible, reason = classify_retrieval_eligibility(row)
        self.assertFalse(eligible)
        self.assertEqual(reason, "EXCLUDED_HISTORICAL")

    def test_retrieval_ineligible_flag(self):
        row = {"currentness_state": "current", "retrieval_eligible": 0, "canonical_status": "candidate"}
        eligible, reason = classify_retrieval_eligibility(row)
        self.assertFalse(eligible)
        self.assertEqual(reason, "EXCLUDED_RETRIEVAL_INELIGIBLE_FLAG")

    def test_unapproved_canonical(self):
        row = {"currentness_state": "current", "retrieval_eligible": 1, "canonical_status": "canonical", "validation_state": "needs_review"}
        eligible, reason = classify_retrieval_eligibility(row)
        self.assertFalse(eligible)
        self.assertEqual(reason, "EXCLUDED_UNAPPROVED_CANONICAL")

    def test_unsupported_status(self):
        row = {"currentness_state": "current", "retrieval_eligible": 1, "canonical_status": "rejected", "validation_state": "unreviewed"}
        eligible, reason = classify_retrieval_eligibility(row)
        self.assertFalse(eligible)
        self.assertEqual(reason, "EXCLUDED_STATUS_NOT_SELECTED")

    def test_no_supporting_evidence(self):
        row = {"currentness_state": "current", "retrieval_eligible": 1, "canonical_status": "candidate", "validation_state": "needs_review"}
        eligible, reason = classify_retrieval_eligibility(row, has_supporting_evidence=False)
        self.assertFalse(eligible)
        self.assertEqual(reason, "EXCLUDED_NO_SUPPORTING_EVIDENCE")

    def test_eligible_approved_canonical(self):
        row = {
            "currentness_state": "current",
            "retrieval_eligible": 1,
            "canonical_status": "canonical",
            "validation_state": "approved",
            "classification": "internal",
            "allowed_audiences_json": '["internal_consulting"]',
            "extraction_run_id": "run-001",
            "origin_type": "human_source",
        }
        eligible, reason = classify_retrieval_eligibility(row, has_supporting_evidence=True)
        self.assertTrue(eligible)
        self.assertEqual(reason, "ELIGIBLE")


class TestGeometryMetrics(unittest.TestCase):
    def test_empty_and_single_vectors(self):
        empty = compute_geometry_metrics([])
        self.assertEqual(empty["count"], 0)
        self.assertEqual(empty["mean_pairwise_cos"], 0.0)

        single = compute_geometry_metrics([np.array([1.0, 0.0, 0.0])])
        self.assertEqual(single["count"], 1)
        self.assertEqual(single["near_duplicate_share"], 0.0)

    def test_orthogonal_vectors(self):
        v1 = np.array([1.0, 0.0])
        v2 = np.array([0.0, 1.0])
        res = compute_geometry_metrics([v1, v2])
        self.assertEqual(res["count"], 2)
        self.assertAlmostEqual(res["mean_pairwise_cos"], 0.0, places=4)
        self.assertEqual(res["near_duplicate_share"], 0.0)

    def test_near_duplicate_vectors(self):
        v1 = np.array([1.0, 0.0, 0.0])
        v2 = np.array([0.999, 0.01, 0.0])
        res = compute_geometry_metrics([v1, v2])
        self.assertEqual(res["count"], 2)
        self.assertGreater(res["median_nn_cos"], 0.95)
        self.assertEqual(res["near_duplicate_share"], 1.0)


class TestMarginTieRates(unittest.TestCase):
    def test_empty_queries(self):
        res = compute_margin_tie_rates({"edge": [np.array([1.0, 0.0])]}, [])
        self.assertEqual(res["edge"]["tie_rate"], 0.0)

    def test_fewer_than_two_vectors(self):
        res = compute_margin_tie_rates({"single": [np.array([1.0, 0.0])]}, [np.array([1.0, 0.0])])
        self.assertEqual(res["single"]["tie_rate"], 1.0)

    def test_margin_tie_and_separation(self):
        # Two identical vectors -> margin delta is 0 -> tie rate is 1.0
        v1 = np.array([1.0, 0.0])
        v2 = np.array([1.0, 0.0])
        q1 = np.array([1.0, 0.0])
        res = compute_margin_tie_rates({"tied": [v1, v2]}, [q1], margin_threshold=0.15)
        self.assertEqual(res["tied"]["tie_rate"], 1.0)

        # Well-separated vectors
        v_a = np.array([1.0, 0.0])
        v_b = np.array([0.0, 1.0])
        res_sep = compute_margin_tie_rates({"separated": [v_a, v_b]}, [q1], margin_threshold=0.15)
        self.assertEqual(res_sep["separated"]["tie_rate"], 0.0)


class TestReachabilityAuditFixture(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.tmp_dir.name) / "test_graph.sqlite"
        self.sidecar_path = Path(self.tmp_dir.name) / "test_sidecar.sqlite"

        # Init graph
        g_conn = sqlite3.connect(self.graph_path)
        g_conn.executescript("""
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                object_node_id TEXT,
                predicate TEXT,
                canonical_status TEXT,
                validation_state TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                classification TEXT,
                allowed_audiences_json TEXT,
                origin_type TEXT,
                extraction_run_id TEXT,
                hitl_required INTEGER,
                validation_priority TEXT,
                validation_owner TEXT,
                atomicity_state TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT
            );
        """)
        # Insert 3 assertions:
        # a1: directly vectorized
        # a2: reachable via profile on node_1
        # a3: unreachable excluded by policy
        g_conn.execute("""
            INSERT INTO assertion VALUES (
                'asrt_1', 'node_1', 'node_2', 'RELATES', 'canonical', 'approved', 'current', 1,
                'internal', '["internal_consulting"]', 'human_source', 'run-1', 0, 'standard', 'owner', 'atomic'
            )
        """)
        g_conn.execute("""
            INSERT INTO assertion VALUES (
                'asrt_2', 'node_1', 'node_3', 'RELATES', 'canonical', 'approved', 'current', 1,
                'internal', '["internal_consulting"]', 'human_source', 'run-1', 0, 'standard', 'owner', 'atomic'
            )
        """)
        g_conn.execute("""
            INSERT INTO assertion VALUES (
                'asrt_3', 'node_9', 'node_10', 'RELATES', 'canonical', 'needs_review', 'current', 1,
                'internal', '["internal_consulting"]', 'human_source', 'run-1', 0, 'standard', 'owner', 'atomic'
            )
        """)
        g_conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_1', 'ev_1', 'supports')")
        g_conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_2', 'ev_2', 'supports')")
        g_conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_3', 'ev_3', 'supports')")
        g_conn.commit()
        g_conn.close()

        # Init sidecar
        s_conn = sqlite3.connect(self.sidecar_path)
        s_conn.executescript("""
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE retrieval_record (
                record_id TEXT PRIMARY KEY,
                record_type TEXT,
                artifact_json TEXT
            );
            CREATE TABLE dense_vector (
                record_id TEXT PRIMARY KEY,
                dimensions INTEGER,
                vector BLOB
            );
        """)
        s_conn.execute("INSERT INTO metadata VALUES ('generation_id', 'gen-test-01')")
        s_conn.execute("INSERT INTO metadata VALUES ('model_id', 'BAAI/bge-m3')")
        s_conn.execute("INSERT INTO metadata VALUES ('model_revision', 'test-rev')")
        s_conn.execute("INSERT INTO metadata VALUES ('embedding_backend', 'flagembedding-bge-m3-dev')")

        # Record for asrt_1
        art_1 = {
            "record_id": "rec_1",
            "record_type": "assertion",
            "retrieval_text": "Sample text for assertion 1",
            "extensions": {"com.partner_advisory.nee.graph-object-id": "asrt_1"},
        }
        vec_1 = np.array([1.0, 0.0], dtype="<f4").tobytes()
        s_conn.execute("INSERT INTO retrieval_record VALUES ('rec_1', 'assertion', ?)", (json.dumps(art_1),))
        s_conn.execute("INSERT INTO dense_vector VALUES ('rec_1', 2, ?)", (vec_1,))

        # Profile for node_1
        art_prof = {
            "record_id": "rec_prof",
            "record_type": "entity_facet",
            "retrieval_text": "Entity profile for node_1",
            "canonical_entities": ["node_1"],
            "extensions": {"com.partner_advisory.nee.graph-entity-ids": ["node_1"]},
        }
        vec_prof = np.array([0.0, 1.0], dtype="<f4").tobytes()
        s_conn.execute("INSERT INTO retrieval_record VALUES ('rec_prof', 'entity_facet', ?)", (json.dumps(art_prof),))
        s_conn.execute("INSERT INTO dense_vector VALUES ('rec_prof', 2, ?)", (vec_prof,))
        s_conn.commit()
        s_conn.close()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_audit_fixture_reachability(self):
        report = audit_semantic_units_and_reachability(
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
        )
        self.assertEqual(report["summary"]["total_current_assertions"], 3)
        self.assertEqual(report["summary"]["reachable_direct_count"], 1)
        self.assertEqual(report["summary"]["reachable_via_profile_count"], 1)
        self.assertEqual(report["summary"]["excluded_policy_count"], 1)
        self.assertEqual(report["summary"]["unreachable_unexplained_count"], 0)
        self.assertEqual(report["summary"]["total_reachable_count"], 2)

        formatted = format_reachability_report(report)
        self.assertIn("Semantic Unit & Reachability Audit Report", formatted)
        self.assertIn("ZERO GAP", formatted)


if __name__ == "__main__":
    unittest.main()
