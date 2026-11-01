"""Unit and edge-case tests for GS-10 GraphSignalEvaluator."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import connect_sqlite
from dual_pipeline.graph_signal_core import (
    evaluate_differential_signal,
    evaluate_graph_signal,
)


class GraphSignalEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.graph_path = self.dir_path / "test_graph.sqlite"
        self.sidecar_path = self.dir_path / "test_sidecar.sqlite"

        # Create minimal graph schema
        conn = connect_sqlite(self.graph_path)
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL
            );
            CREATE TABLE source (
                source_id TEXT PRIMARY KEY,
                source_name TEXT NOT NULL
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                object_literal TEXT,
                assertion_type TEXT,
                confidence_score REAL,
                origin_type TEXT,
                validation_state TEXT,
                currentness_state TEXT NOT NULL,
                canonical_status TEXT NOT NULL,
                recorded_at TEXT,
                created_at TEXT,
                primary_source_id TEXT NOT NULL
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_relationship TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()

        # Create minimal sidecar schema
        conn_s = connect_sqlite(self.sidecar_path)
        conn_s.executescript(
            """
            CREATE TABLE retrieval_record (
                record_id TEXT PRIMARY KEY,
                lane TEXT NOT NULL,
                artifact_json TEXT NOT NULL
            );
            """
        )
        conn_s.commit()
        conn_s.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_evaluate_clean_graph(self) -> None:
        conn = connect_sqlite(self.graph_path)
        conn.execute("INSERT INTO node VALUES ('node_1', 'Node 1'), ('node_2', 'Node 2')")
        conn.execute("INSERT INTO source VALUES ('src_1', 'Source 1')")
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt_1', 'node_1', 'OWNS_OR_LEADS', 'node_2', NULL, 'edge', 0.95, 'verified', 'approved', 'current', 'canonical', '2026-09-25T12:00:00Z', '2026-09-25T12:00:00Z', 'src_1')"""
        )
        conn.execute(
            "INSERT INTO assertion_evidence VALUES ('asrt_1', 'ev_1', 'supports')"
        )
        conn.commit()
        conn.close()

        conn_s = connect_sqlite(self.sidecar_path)
        conn_s.execute(
            """INSERT INTO retrieval_record VALUES
               ('rec_1', 'approved', '{"currentness_state": "current", "canonical_status": "canonical"}')"""
        )
        conn_s.commit()
        conn_s.close()

        result = evaluate_graph_signal(self.graph_path, self.sidecar_path)
        m = result["metrics"]
        self.assertEqual(m["v_active"], 2)
        self.assertEqual(m["e_active"], 1)
        self.assertEqual(m["density_rho"], 0.5)
        self.assertEqual(m["typedness_tau"], 1.0)
        self.assertEqual(m["confidence_mean"], 0.95)
        self.assertEqual(m["grounding_depth_gamma"], 1.0)
        self.assertEqual(m["mutex_conflict_entropy_h"], 0)
        self.assertEqual(m["approved_lane_purity_pi"], 1.0)
        self.assertEqual(m["orphan_edge_ratio"], 0.0)
        self.assertGreater(m["composite_score_q"], 85.0)

    def test_differential_signal_pass(self) -> None:
        pre_metrics = {
            "v_active": 10,
            "e_active": 20,
            "density_rho": 2.0,
            "typedness_tau": 0.8,
            "confidence_mean": 0.85,
            "grounding_depth_gamma": 1.0,
            "temporal_freshness_phi": 0.7,
            "mutex_conflict_entropy_h": 0,
            "approved_lane_purity_pi": 1.0,
            "orphan_edge_ratio": 0.0,
            "trait_completeness_lambda": 0.95,
            "composite_score_q": 88.0,
        }
        post_metrics = dict(pre_metrics)
        post_metrics["composite_score_q"] = 90.0
        post_metrics["temporal_freshness_phi"] = 0.8

        diff = evaluate_differential_signal(
            {"metrics": pre_metrics}, {"metrics": post_metrics}
        )
        self.assertTrue(diff["passed"])
        self.assertEqual(diff["verdict"], "PASS")
        self.assertEqual(len(diff["violations"]), 0)

    def test_differential_signal_fail_on_mutex_conflict(self) -> None:
        pre_metrics = {
            "v_active": 10,
            "e_active": 20,
            "density_rho": 2.0,
            "typedness_tau": 0.8,
            "confidence_mean": 0.85,
            "grounding_depth_gamma": 1.0,
            "temporal_freshness_phi": 0.7,
            "mutex_conflict_entropy_h": 0,
            "approved_lane_purity_pi": 1.0,
            "orphan_edge_ratio": 0.0,
            "trait_completeness_lambda": 0.95,
            "composite_score_q": 88.0,
        }
        post_metrics = dict(pre_metrics)
        post_metrics["mutex_conflict_entropy_h"] = 1  # Conflict introduced!

        diff = evaluate_differential_signal(
            {"metrics": pre_metrics}, {"metrics": post_metrics}
        )
        self.assertFalse(diff["passed"])
        self.assertEqual(diff["verdict"], "FAIL_CLOSED")
        self.assertTrue(any("MUTEX_CONFLICTS_PRESENT" in v for v in diff["violations"]))


if __name__ == "__main__":
    unittest.main()
