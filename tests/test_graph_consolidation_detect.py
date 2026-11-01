"""
tests/test_graph_consolidation_detect.py

Unit and regression tests for Graph Consolidation Wave 2 detection engine.
Validates node clustering, assertion supersession, evidence consolidation,
and plan construction determinism against empirical graph state.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_consolidation_core.detect.assertions import detect_assertion_overlaps
from dual_pipeline.graph_consolidation_core.detect.evidence import detect_evidence_fragmentation
from dual_pipeline.graph_consolidation_core.detect.nodes import detect_node_clusters
from dual_pipeline.graph_consolidation_core.plan import build_consolidation_plan
from dual_pipeline.release import ReleaseManager
from tools.collect_graph_consolidation_baseline import (
    DEFAULT_RUNTIME_ROOT,
)


class TestGraphConsolidationDetect(unittest.TestCase):
    """Unit tests for detection and planning algorithms in graph_consolidation_core."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_graph.sqlite"
        self._init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                display_name TEXT,
                description TEXT,
                status TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z',
                updated_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z'
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                object_literal TEXT,
                assertion_type TEXT NOT NULL DEFAULT 'relationship',
                confidence_label TEXT DEFAULT 'HIGH',
                primary_source_id TEXT,
                extraction_run_id TEXT,
                currentness_state TEXT NOT NULL DEFAULT 'current',
                canonical_status TEXT NOT NULL DEFAULT 'canonical',
                retrieval_eligible INTEGER NOT NULL DEFAULT 1,
                confidence_score REAL NOT NULL DEFAULT 1.0,
                recorded_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z',
                created_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z',
                updated_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z',
                superseded_by_assertion_id TEXT
            );
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                evidence_fingerprint TEXT,
                source_id TEXT NOT NULL,
                source_version_id TEXT,
                locator_kind TEXT,
                locator_value TEXT NOT NULL,
                direct_quote TEXT,
                source_authority_rank INTEGER NOT NULL DEFAULT 1,
                extraction_confidence REAL NOT NULL DEFAULT 1.0,
                corroboration_group TEXT,
                created_at TEXT NOT NULL DEFAULT '2026-07-08T00:00:00Z'
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                PRIMARY KEY (assertion_id, evidence_id)
            );
            """
        )
        conn.commit()
        conn.close()

    def test_empty_database_returns_clean_zero_plan(self) -> None:
        """Empty graph returns empty findings and proposed plan."""
        plan = build_consolidation_plan(self.db_path)
        self.assertEqual(plan["status"], "proposed")
        self.assertTrue(plan["approval_required"])
        self.assertFalse(plan["auto_apply"])
        self.assertEqual(plan["summary"]["total_operations_count"], 0)
        self.assertEqual(plan["summary"]["candidate_node_merges_count"], 0)
        self.assertEqual(plan["summary"]["candidate_assertion_supersessions_count"], 0)
        self.assertEqual(plan["summary"]["candidate_evidence_clusters_count"], 0)
        self.assertIn("plan_id", plan)
        self.assertIn("plan_digest", plan)

    def test_node_clustering_and_hitl_margin(self) -> None:
        """Detects duplicates and evaluates confidence margin delta."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n1', 'Apex Global Enterprises', 'org')"
        )
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n2', 'apex_global energy', 'org')"
        )
        conn.commit()

        findings = detect_node_clusters(conn, similarity_threshold=0.65)
        conn.close()

        self.assertEqual(findings["clusters_total"], 1)
        cluster = findings["clusters"][0]
        self.assertEqual(cluster["cluster_type"], "same_type_exact_name")
        self.assertEqual(cluster["confidence_merge"], 0.95)
        self.assertEqual(cluster["confidence_distinct"], 0.05)
        self.assertFalse(cluster["hitl_required"])
        self.assertEqual(cluster["recommended_action"], "MERGE")

    def test_assertion_supersession_exact_triples(self) -> None:
        """Detects duplicate assertions and selects winner based on confidence/timestamp."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # Ensure subject node exists so JOIN succeeds
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type) VALUES ('sub1', 'Subject One', 'org')"
        )
        # a1 has confidence 1.0, a2 has confidence 0.6
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score) "
            "VALUES ('a1', 'sub1', 'PARTICIPATES_IN', 'obj1', 1.0)"
        )
        conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, confidence_score) "
            "VALUES ('a2', 'sub1', 'PARTICIPATES_IN', 'obj1', 0.6)"
        )
        conn.commit()

        overlaps = detect_assertion_overlaps(conn)
        conn.close()

        self.assertEqual(overlaps["exact_triple_duplicates_count"], 1)
        group = overlaps["exact_triple_groups"][0]
        self.assertEqual(group["winner_id"], "a1")
        self.assertEqual(group["loser_id"], "a2")
        self.assertFalse(group["hitl_required"])
        self.assertAlmostEqual(group["confidence_delta"], 0.4)

    def test_evidence_locator_clustering(self) -> None:
        """Clusters evidence items sharing identical source and locator."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO evidence_item (evidence_id, source_id, locator_kind, locator_value, direct_quote) "
            "VALUES ('ev1', 'src1', 'slide', 'slide_4', 'quote text one')"
        )
        conn.execute(
            "INSERT INTO evidence_item (evidence_id, source_id, locator_kind, locator_value, direct_quote) "
            "VALUES ('ev2', 'src1', 'slide', 'slide_4', 'quote text duplicate')"
        )
        conn.commit()

        res = detect_evidence_fragmentation(conn)
        conn.close()

        self.assertEqual(res["duplicate_locator_groups_count"], 1)
        group = res["locator_clusters"][0]
        self.assertEqual(group["locator_value"], "slide_4")
        self.assertEqual(group["items_count"], 2)

    def test_plan_digest_idempotency(self) -> None:
        """Building consolidation plan twice on unchanged graph yields identical plan_digest."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n1', 'Test Corp', 'org')"
        )
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n2', 'test corp', 'org')"
        )
        conn.commit()
        conn.close()

        plan1 = build_consolidation_plan(self.db_path)
        plan2 = build_consolidation_plan(self.db_path)
        self.assertEqual(plan1["plan_digest"], plan2["plan_digest"])
        self.assertEqual(plan1["summary"], plan2["summary"])

    def test_active_graph_detection_probes(self) -> None:
        """Runs detection against the real active graph and verifies baseline figures."""
        manager = ReleaseManager(DEFAULT_RUNTIME_ROOT)
        active = manager.active_pair()
        graph_path = Path(manager._resolve(active["graph_path"])).resolve()
        self.assertTrue(graph_path.exists())

        conn = sqlite3.connect(str(graph_path))
        conn.row_factory = sqlite3.Row
        try:
            node_res = detect_node_clusters(conn)
            asrt_res = detect_assertion_overlaps(conn)
            ev_res = detect_evidence_fragmentation(conn)
        finally:
            conn.close()

        self.assertGreaterEqual(node_res["clusters_total"], 1)
        self.assertGreaterEqual(asrt_res["exact_triple_duplicates_count"], 0)
        self.assertGreaterEqual(ev_res["duplicate_locator_groups_count"], 1)

    def test_single_pane_of_glass_three_node_containment_clustering(self) -> None:
        """Detects and clusters all three Single Pane of Glass variants into canonical program survivor."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executemany(
            "INSERT INTO node (node_id, canonical_name, node_type, status) VALUES (?, ?, ?, ?)",
            [
                (
                    "entity-5a6c1d0b05e28c3caa71cab2600e",
                    "Meridian Utility Corp Distribution Single Pane of Glass",
                    "program",
                    "candidate",
                ),
                (
                    "entity-053fabdcf49740a3b9179648bcd9",
                    "Single Pane of Glass",
                    "program",
                    "candidate",
                ),
                (
                    "entity-735bcda5de54a8af079efe336800",
                    "Dominion single-pane-of-glass application",
                    "technical_anchor",
                    "candidate",
                ),
            ],
        )
        # Attach higher assertion connectivity to the Doc 1 canonical program node
        for idx in range(5):
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal) VALUES (?, ?, 'HAS_CLAIM_TEXT', 'claim')",
                (f"asrt-{idx}", "entity-5a6c1d0b05e28c3caa71cab2600e"),
            )
        conn.commit()

        findings = detect_node_clusters(conn, similarity_threshold=0.65)
        conn.close()

        self.assertEqual(findings["clusters_total"], 3)
        survivors_for_canon = [
            c["recommended_survivor_id"]
            for c in findings["clusters"]
            if "entity-5a6c1d0b05e28c3caa71cab2600e" in (c["node_a"]["node_id"], c["node_b"]["node_id"])
        ]
        self.assertEqual(
            survivors_for_canon,
            ["entity-5a6c1d0b05e28c3caa71cab2600e", "entity-5a6c1d0b05e28c3caa71cab2600e"],
        )


if __name__ == "__main__":
    unittest.main()
