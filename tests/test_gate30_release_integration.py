"""Unit tests for Gate 30 GS-10 Graph Signal Non-Degradation Release Gate Integration."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import connect_sqlite
from dual_pipeline.graph_signal_core import (
    evaluate_differential_signal,
    evaluate_graph_signal,
)
from dual_pipeline.release_core.models import _PrepareManyState


class Gate30ReleaseIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.pre_graph = self.dir_path / "pre_graph.sqlite"
        self.pre_sidecar = self.dir_path / "pre_sidecar.sqlite"
        self.post_graph = self.dir_path / "post_graph.sqlite"
        self.post_sidecar = self.dir_path / "post_sidecar.sqlite"

        for p in (self.pre_graph, self.post_graph):
            conn = connect_sqlite(p)
            conn.executescript(
                """
                CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL);
                CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL);
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

    def test_prepare_many_state_has_graph_signal_evaluation(self) -> None:
        state = _PrepareManyState(
            run_ids=["run_1"],
            approval_id="app_1",
            backend=None,  # type: ignore
        )
        self.assertIsNotNone(state.graph_signal_evaluation)
        self.assertEqual(state.graph_signal_evaluation, {})

    def test_gate30_passes_on_improving_signal(self) -> None:
        # Pre baseline: 1 assertion
        c_pre = connect_sqlite(self.pre_graph)
        c_pre.execute("INSERT INTO node VALUES ('node_1', 'Node 1'), ('workstream_ea', 'EA')")
        c_pre.execute("INSERT INTO source VALUES ('src_1', 'Source 1')")
        c_pre.execute(
            """INSERT INTO assertion VALUES
               ('asrt_1', 'node_1', 'OWNS_OR_LEADS', 'workstream_ea', NULL, 'edge', 0.85, 'v', 'app', 'current', 'canonical', '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z', 'src_1')"""
        )
        c_pre.execute("INSERT INTO assertion_evidence VALUES ('asrt_1', 'ev_1', 'supports')")
        c_pre.commit()
        c_pre.close()

        c_pres = connect_sqlite(self.pre_sidecar)
        c_pres.execute("INSERT INTO retrieval_record VALUES ('r1', 'approved', '{\"currentness_state\":\"current\",\"canonical_status\":\"canonical\"}')")
        c_pres.commit()
        c_pres.close()

        # Post state: 2 assertions, density improves, zero mutex conflicts
        c_post = connect_sqlite(self.post_graph)
        c_post.execute("INSERT INTO node VALUES ('node_1', 'Node 1'), ('node_2', 'Node 2'), ('workstream_ea', 'EA')")
        c_post.execute("INSERT INTO source VALUES ('src_1', 'Source 1')")
        c_post.execute(
            """INSERT INTO assertion VALUES
               ('asrt_1', 'node_1', 'OWNS_OR_LEADS', 'workstream_ea', NULL, 'edge', 0.90, 'v', 'app', 'current', 'canonical', '2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z', 'src_1'),
               ('asrt_2', 'node_2', 'CONTRIBUTES_TO', 'workstream_ea', NULL, 'edge', 0.90, 'v', 'app', 'current', 'canonical', '2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z', 'src_1')"""
        )
        c_post.execute("INSERT INTO assertion_evidence VALUES ('asrt_1', 'ev_1', 'supports'), ('asrt_2', 'ev_2', 'supports')")
        c_post.commit()
        c_post.close()

        c_posts = connect_sqlite(self.post_sidecar)
        c_posts.execute("INSERT INTO retrieval_record VALUES ('r1', 'approved', '{\"currentness_state\":\"current\",\"canonical_status\":\"canonical\"}'), ('r2', 'approved', '{\"currentness_state\":\"current\",\"canonical_status\":\"canonical\"}')")
        c_posts.commit()
        c_posts.close()

        pre_eval = evaluate_graph_signal(self.pre_graph, self.pre_sidecar)
        post_eval = evaluate_graph_signal(self.post_graph, self.post_sidecar)
        diff_eval = evaluate_differential_signal(pre_eval, post_eval)

        self.assertTrue(diff_eval["passed"])
        self.assertEqual(diff_eval["verdict"], "PASS")
        self.assertEqual(len(diff_eval["violations"]), 0)

    def test_gate30_fails_on_mutex_collision(self) -> None:
        # Pre baseline: 1 assertion
        c_pre = connect_sqlite(self.pre_graph)
        c_pre.execute("INSERT INTO node VALUES ('node_1', 'Node 1'), ('workstream_ea', 'EA')")
        c_pre.execute("INSERT INTO source VALUES ('src_1', 'Source 1')")
        c_pre.execute(
            """INSERT INTO assertion VALUES
               ('asrt_1', 'node_1', 'OWNS_OR_LEADS', 'workstream_ea', NULL, 'edge', 0.85, 'v', 'app', 'current', 'canonical', '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z', 'src_1')"""
        )
        c_pre.execute("INSERT INTO assertion_evidence VALUES ('asrt_1', 'ev_1', 'supports')")
        c_pre.commit()
        c_pre.close()

        # Post state: introduces a SECOND active lead on workstream_ea (H_mutex = 1)
        c_post = connect_sqlite(self.post_graph)
        c_post.execute("INSERT INTO node VALUES ('node_1', 'Node 1'), ('node_2', 'Node 2'), ('workstream_ea', 'EA')")
        c_post.execute("INSERT INTO source VALUES ('src_1', 'Source 1')")
        c_post.execute(
            """INSERT INTO assertion VALUES
               ('asrt_1', 'node_1', 'OWNS_OR_LEADS', 'workstream_ea', NULL, 'edge', 0.85, 'v', 'app', 'current', 'canonical', '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z', 'src_1'),
               ('asrt_2', 'node_2', 'OWNS_OR_LEADS', 'workstream_ea', NULL, 'edge', 0.85, 'v', 'app', 'current', 'canonical', '2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z', 'src_1')"""
        )
        c_post.execute("INSERT INTO assertion_evidence VALUES ('asrt_1', 'ev_1', 'supports'), ('asrt_2', 'ev_2', 'supports')")
        c_post.commit()
        c_post.close()

        pre_eval = evaluate_graph_signal(self.pre_graph, self.pre_sidecar)
        post_eval = evaluate_graph_signal(self.post_graph, self.post_sidecar)
        diff_eval = evaluate_differential_signal(pre_eval, post_eval)

        # Gate 30 MUST FAIL
        self.assertFalse(diff_eval["passed"])
        self.assertEqual(diff_eval["verdict"], "FAIL_CLOSED")
        self.assertTrue(any("MUTEX_CONFLICTS_PRESENT" in v for v in diff_eval["violations"]))


if __name__ == "__main__":
    unittest.main()
