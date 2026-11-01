"""Adversarial and boundary tests for semantic unit reachability audit."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dual_pipeline.index_core.reachability_audit import (
    audit_semantic_units_and_reachability,
    compute_geometry_metrics,
)


class TestReachabilityAuditAdversarial(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.tmp_dir.name) / "adv_graph.sqlite"
        self.sidecar_path = Path(self.tmp_dir.name) / "adv_sidecar.sqlite"

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _init_empty_dbs(self):
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
        g_conn.commit()
        g_conn.close()

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
        s_conn.commit()
        s_conn.close()

    def test_adversarial_1_missing_files_fail_closed(self):
        with self.assertRaises(FileNotFoundError):
            audit_semantic_units_and_reachability(
                graph_path=Path("/tmp/non_existent_graph_99999.sqlite"),
                sidecar_path=self.sidecar_path,
            )

        with self.assertRaises(FileNotFoundError):
            audit_semantic_units_and_reachability(
                graph_path=self.graph_path,
                sidecar_path=Path("/tmp/non_existent_sidecar_99999.sqlite"),
            )

    def test_adversarial_2_empty_database_boundary(self):
        self._init_empty_dbs()
        report = audit_semantic_units_and_reachability(
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
        )
        self.assertEqual(report["summary"]["total_current_assertions"], 0)
        self.assertEqual(report["summary"]["total_reachable_count"], 0)
        self.assertEqual(report["summary"]["unreachable_unexplained_count"], 0)
        self.assertEqual(len(report["unit_type_inventory"]), 0)

    def test_adversarial_3_corrupted_blobs_and_malformed_json(self):
        self._init_empty_dbs()
        s_conn = sqlite3.connect(self.sidecar_path)
        # Corrupted vector blob (odd byte length)
        s_conn.execute("INSERT INTO retrieval_record VALUES ('rec_corrupt', 'edge', '{\"retrieval_text\": \"edge text\"}')")
        s_conn.execute("INSERT INTO dense_vector VALUES ('rec_corrupt', 2, X'010203')")

        # Invalid/empty JSON artifact
        s_conn.execute("INSERT INTO retrieval_record VALUES ('rec_bad_json', 'edge', '{}')")
        s_conn.commit()
        s_conn.close()

        # Should not crash and should record vector count as 0 for corrupt blob
        report = audit_semantic_units_and_reachability(
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
        )
        self.assertIn("edge", report["unit_type_inventory"])
        self.assertEqual(report["unit_type_inventory"]["edge"]["vector_count"], 0)
        self.assertEqual(report["unit_type_inventory"]["edge"]["record_count"], 2)

    def test_adversarial_4_unexplained_gap_detection(self):
        self._init_empty_dbs()
        g_conn = sqlite3.connect(self.graph_path)
        # Insert an eligible assertion that is NOT in the sidecar and has NO profile link
        g_conn.execute("""
            INSERT INTO assertion VALUES (
                'orphan_eligible_assertion', 'unseen_node_1', 'unseen_node_2', 'RELATES', 'canonical', 'approved', 'current', 1,
                'internal', '["internal_consulting"]', 'human_source', 'run-test', 0, 'standard', 'owner', 'atomic'
            )
        """)
        g_conn.execute("INSERT INTO assertion_evidence VALUES ('orphan_eligible_assertion', 'ev_orphan', 'supports')")
        g_conn.commit()
        g_conn.close()

        # Audit must detect this as an UNREACHABLE_UNEXPLAINED defect!
        report = audit_semantic_units_and_reachability(
            graph_path=self.graph_path,
            sidecar_path=self.sidecar_path,
        )
        self.assertEqual(report["summary"]["total_current_assertions"], 1)
        self.assertEqual(report["summary"]["total_reachable_count"], 0)
        self.assertEqual(report["summary"]["unreachable_unexplained_count"], 1)
        self.assertIn("orphan_eligible_assertion", report["reachability_breakdown"]["unreachable_unexplained_ids"])

    def test_adversarial_5_degenerate_vector_geometry(self):
        # All-zero vectors and extreme norms
        v_zero = np.array([0.0, 0.0, 0.0])
        v_tiny = np.array([1e-15, 1e-15, 1e-15])
        metrics = compute_geometry_metrics([v_zero, v_tiny])
        self.assertEqual(metrics["count"], 2)
        self.assertFalse(np.isnan(metrics["mean_pairwise_cos"]))
        self.assertFalse(np.isinf(metrics["mean_pairwise_cos"]))


if __name__ == "__main__":
    unittest.main()
