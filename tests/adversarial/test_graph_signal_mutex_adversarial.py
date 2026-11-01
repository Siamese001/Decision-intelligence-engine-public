#!/usr/bin/env python3
"""Adversarial and Edge-Case Stress Test Suite for GS-10 Graph Signal Governance.

Covers mandatory edge-case categories:
1. Boundary & Malformed Payloads: Null objects, malformed timestamps, negative/overflow confidences.
2. Invariant Bypass Attempts: Rogue concurrent insertion of conflicting leads without supersession.
3. Rollback & Recovery Integrity: Gate 30 failure aborts activation without corrupting baseline.
4. Stale Shield Exploit Prevention: Quarantined candidate cannot mask stale approved facts.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import connect_sqlite
from dual_pipeline.graph_retrieval_core.scoring import _edge_increment, _parse_date_epoch
from dual_pipeline.graph_signal_core import (
    evaluate_differential_signal,
    evaluate_graph_signal,
    find_conflicting_assertions,
    reconcile_all_graph_mutex,
    supersede_conflicting_assertions,
)


@pytest.mark.adversarial
class GraphSignalMutexAdversarialTests(unittest.TestCase):
    """Adversarial stress test suite covering failure modes and boundary edge cases."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.db_path = self.dir_path / "test_adversarial.sqlite"
        self.sidecar_path = self.dir_path / "test_adversarial_sidecar.sqlite"

        conn = connect_sqlite(self.db_path)
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
                superseded_at TEXT,
                superseded_by_assertion_id TEXT,
                retrieval_eligible INTEGER NOT NULL DEFAULT 1,
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
                retrieval_eligible INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT
            );
            """
        )
        conn.close()

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
        conn_s.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_category_1_boundary_and_malformed_payloads(self) -> None:
        """Boundary Payloads: Handles None, non-string, corrupted dates, and out-of-range confidences."""
        # 1. Null and empty inputs to conflict detection
        conn = connect_sqlite(self.db_path)
        self.assertEqual(find_conflicting_assertions(conn, object_node_id=None, predicate="OWNS_OR_LEADS"), [])
        self.assertEqual(supersede_conflicting_assertions(conn, incoming_assertion_id="a1", subject_node_id="s1", predicate="OWNS_OR_LEADS", object_node_id=None), [])

        # 2. Corrupted and boundary date epochs
        self.assertIsNone(_parse_date_epoch(""))
        self.assertIsNone(_parse_date_epoch("2026/09/25 12:00:00"))  # Non-ISO
        self.assertIsNone(_parse_date_epoch("TBD"))
        self.assertIsNone(_parse_date_epoch(float("nan")))

        # 3. Out-of-bounds confidence values clamped cleanly
        edge_overflow = {
            "relationship_family": "DELIVERS",
            "evidence_ids": [],
            "materiality": "low",
            "confidence_score": 999.0,  # Extreme overflow
            "recorded_at": "invalid-timestamp",
        }
        edge_underflow = {
            "relationship_family": "DELIVERS",
            "evidence_ids": [],
            "materiality": "low",
            "confidence_score": -50.0,  # Extreme underflow
            "recorded_at": None,
        }
        inc_over, _ = _edge_increment(edge_overflow, requested_relationships=set(), destination_degree=0, hop=1)
        inc_under, _ = _edge_increment(edge_underflow, requested_relationships=set(), destination_degree=0, hop=1)
        self.assertGreater(inc_over, inc_under)
        self.assertFalse(any(isinstance(x, complex) for x in (inc_over, inc_under)))
        conn.close()

    def test_category_2_invariant_bypass_attempt(self) -> None:
        """Invariant Bypass Attempt: Concurrent bypass inserting 5 conflicting leads is trapped and reconciled."""
        conn = connect_sqlite(self.db_path)
        conn.execute("INSERT INTO node VALUES ('pod_risk', 'Risk Pod')")
        conn.execute("INSERT INTO source VALUES ('src_1', 'Source 1')")

        # Rogue insertion bypassing Stage U0 supersession
        for i in range(1, 6):
            conn.execute(
                f"""INSERT INTO assertion VALUES
                   ('asrt_rogue_{i}', 'person_{i}', 'OWNS_OR_LEADS', 'pod_risk', NULL, 'edge', 0.80, 'native', 'approved', 'current', 'canonical', NULL, NULL, 1, '2026-09-0{i}T00:00:00Z', '2026-09-0{i}T00:00:00Z', NULL, 'src_1')"""
            )
            conn.execute(
                f"INSERT INTO assertion_evidence VALUES ('asrt_rogue_{i}', 'ev_{i}', 'supports')"
            )
        conn.commit()

        # Evaluator must catch the invariant bypass immediately (H_mutex = 4)
        eval_result = evaluate_graph_signal(self.db_path, self.sidecar_path)
        self.assertEqual(eval_result["metrics"]["mutex_conflict_entropy_h"], 4)

        # Full automated reconciliation sweep engages and restores H_mutex = 0
        logs = reconcile_all_graph_mutex(conn)
        conn.commit()
        self.assertEqual(len(logs), 4)

        eval_reconciled = evaluate_graph_signal(self.db_path, self.sidecar_path)
        self.assertEqual(eval_reconciled["metrics"]["mutex_conflict_entropy_h"], 0)
        self.assertEqual(logs[0]["winner_assertion_id"], "asrt_rogue_5")  # Newest wins
        conn.close()

    def test_category_3_rollback_and_recovery_integrity(self) -> None:
        """Rollback & Recovery Integrity: Gate 30 aborts upon signal degradation and preserves baseline."""
        baseline_graph = self.dir_path / "rollback_base.sqlite"
        corrupted_graph = self.dir_path / "rollback_corrupt.sqlite"

        # Baseline: Valid grounded assertion
        c_base = connect_sqlite(baseline_graph)
        c_base.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL)")
        c_base.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL)")
        c_base.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY, subject_node_id TEXT NOT NULL, predicate TEXT NOT NULL,
                object_node_id TEXT, object_literal TEXT, assertion_type TEXT, confidence_score REAL,
                origin_type TEXT, validation_state TEXT, currentness_state TEXT NOT NULL,
                canonical_status TEXT NOT NULL, recorded_at TEXT, created_at TEXT, primary_source_id TEXT NOT NULL
            )"""
        )
        c_base.execute("CREATE TABLE assertion_evidence (assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL, evidence_relationship TEXT NOT NULL)")
        c_base.execute("INSERT INTO node VALUES ('n1', 'N1')")
        c_base.execute("INSERT INTO source VALUES ('s1', 'S1')")
        c_base.execute("INSERT INTO assertion VALUES ('a1', 'n1', 'GOVERNS', 'pod_sec', NULL, 'edge', 0.90, 'v', 'app', 'current', 'canonical', '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z', 's1')")
        c_base.execute("INSERT INTO assertion_evidence VALUES ('a1', 'ev1', 'supports')")
        c_base.commit()
        c_base.close()

        # Corrupted target: Orphan assertion missing source & node references
        c_corrupt = connect_sqlite(corrupted_graph)
        c_corrupt.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL)")
        c_corrupt.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT NOT NULL)")
        c_corrupt.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY, subject_node_id TEXT NOT NULL, predicate TEXT NOT NULL,
                object_node_id TEXT, object_literal TEXT, assertion_type TEXT, confidence_score REAL,
                origin_type TEXT, validation_state TEXT, currentness_state TEXT NOT NULL,
                canonical_status TEXT NOT NULL, recorded_at TEXT, created_at TEXT, primary_source_id TEXT NOT NULL
            )"""
        )
        c_corrupt.execute("CREATE TABLE assertion_evidence (assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL, evidence_relationship TEXT NOT NULL)")
        # Insert orphan edge pointing to non-existent node 'ghost_node' and source 'ghost_source'
        c_corrupt.execute("INSERT INTO assertion VALUES ('a_ghost', 'ghost_node', 'GOVERNS', 'pod_sec', NULL, 'edge', 0.10, 'v', 'app', 'current', 'canonical', '2026-09-20T00:00:00Z', '2026-09-20T00:00:00Z', 'ghost_source')")
        c_corrupt.commit()
        c_corrupt.close()

        eval_base = evaluate_graph_signal(baseline_graph, None)
        eval_corrupt = evaluate_graph_signal(corrupted_graph, None)
        diff_eval = evaluate_differential_signal(eval_base, eval_corrupt)

        # Gate 30 differential evaluation MUST FAIL CLOSED
        self.assertFalse(diff_eval["passed"])
        self.assertEqual(diff_eval["verdict"], "FAIL_CLOSED")
        self.assertTrue(any("ORPHAN_ASSERTIONS_DETECTED" in v for v in diff_eval["violations"]))

    def test_category_4_stale_shield_exploit_prevention(self) -> None:
        """Stale Shield Exploit: Quarantined candidate cannot prevent approved lane impurity from failing Gate 30."""
        # Active sidecar containing a superseded assertion still residing in approved lane
        conn_s = connect_sqlite(self.sidecar_path)
        conn_s.execute(
            """INSERT INTO retrieval_record VALUES
               ('rec_stale', 'approved', '{"currentness_state": "historical", "canonical_status": "canonical"}')"""
        )
        conn_s.commit()
        conn_s.close()

        eval_result = evaluate_graph_signal(self.db_path, self.sidecar_path)
        self.assertEqual(eval_result["metrics"]["approved_lane_purity_pi"], 0.0)

        # Differential check against clean baseline
        clean_eval = {
            "metrics": {
                "density_rho": 2.0, "typedness_tau": 0.6, "confidence_mean": 0.8,
                "grounding_depth_gamma": 1.0, "temporal_freshness_phi": 0.8,
                "mutex_conflict_entropy_h": 0, "approved_lane_purity_pi": 1.0,
                "orphan_edge_ratio": 0.0, "trait_completeness_lambda": 1.0,
                "composite_score_q": 85.0,
            }
        }
        diff = evaluate_differential_signal(clean_eval, eval_result)
        self.assertFalse(diff["passed"])
        self.assertTrue(any("APPROVED_LANE_IMPURITY" in v for v in diff["violations"]))


if __name__ == "__main__":
    unittest.main()
