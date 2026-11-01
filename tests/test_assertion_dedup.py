"""
tests/test_assertion_dedup.py

Comprehensive tests for Wave 6: Knowledge graph hygiene automation.
Validates semantic assertion deduplication, index pruning, and backtest graph health auditing.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.health_cli import _backtest_health
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.graph_maintenance_core.dedup import (
    apply_supersessions,
    find_semantic_duplicates,
)
from dual_pipeline.graph_maintenance_core.health import (
    calculate_graph_health_metrics,
    render_health_report_markdown,
)
from dual_pipeline.graph_maintenance_core.pruning import (
    prune_superseded_search_documents,
)


class TestAssertionDedupAndHygiene(unittest.TestCase):
    """Test suite for semantic duplicate detection, pruning, and graph health."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.db_path = self.tmp_path / "test_graph.sqlite"
        self._init_db(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT,
                active INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                claim_text TEXT,
                currentness_state TEXT DEFAULT 'current',
                retrieval_eligible INTEGER DEFAULT 1,
                recorded_at REAL,
                created_at REAL,
                superseded_at TEXT,
                superseded_by_assertion_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE search_document (
                search_document_id TEXT PRIMARY KEY,
                assertion_id TEXT,
                currentness_state TEXT DEFAULT 'current',
                retrieval_eligible INTEGER DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE dense_vector (
                search_document_id TEXT PRIMARY KEY,
                vector BLOB
            )
        """)
        conn.commit()
        conn.close()

    def test_find_semantic_duplicates_exact_and_fuzzy(self) -> None:
        """Verify duplicate detection groups by subject/predicate/object and matches claim_text > 0.9."""
        conn = sqlite3.connect(self.db_path)
        # Older assertion (recorded_at = 100)
        conn.execute(
            """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, recorded_at)
               VALUES ('ast-01', 'person:alice', 'AFFILIATED_WITH', 'org:partner_advisory', 'Alice works at PartnerAdvisory Consulting as Director', 100)"""
        )
        # Newer assertion with slight typo/whitespace variation (recorded_at = 200)
        conn.execute(
            """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, recorded_at)
               VALUES ('ast-02', 'person:alice', 'AFFILIATED_WITH', 'org:partner_advisory', 'Alice works at PartnerAdvisory Consulting as Director.', 200)"""
        )
        # Completely different claim text
        conn.execute(
            """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, recorded_at)
               VALUES ('ast-03', 'person:alice', 'AFFILIATED_WITH', 'org:partner_advisory', 'Alice left the company entirely in 2024', 300)"""
        )
        conn.commit()

        proposals = find_semantic_duplicates(conn, similarity_threshold=0.9)
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p["winner_id"], "ast-02")
        self.assertEqual(p["loser_id"], "ast-01")
        self.assertGreaterEqual(p["similarity"], 0.95)
        conn.close()

    def test_apply_supersessions_updates_assertion_and_search_doc(self) -> None:
        """Verify apply_supersessions marks loser assertions historical and updates search_document."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, recorded_at)
               VALUES ('ast-01', 'org:partner_advisory', 'OPERATES_IN', '', 'PartnerAdvisory operates in Seattle, Washington', 100)"""
        )
        conn.execute(
            """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, recorded_at)
               VALUES ('ast-02', 'org:partner_advisory', 'OPERATES_IN', '', 'PartnerAdvisory operates in Seattle, Washington.', 200)"""
        )
        conn.execute(
            "INSERT INTO search_document (search_document_id, assertion_id, retrieval_eligible) VALUES ('doc-01', 'ast-01', 1)"
        )
        conn.commit()

        proposals = find_semantic_duplicates(conn, similarity_threshold=0.9)
        applied = apply_supersessions(conn, proposals)
        self.assertEqual(applied, 1)

        # Check assertion ast-01 is historical
        row = conn.execute("SELECT currentness_state, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id='ast-01'").fetchone()
        self.assertEqual(row[0], "historical")
        self.assertEqual(row[1], 0)
        self.assertEqual(row[2], "ast-02")

        # Check search_document doc-01 is historical
        sd_row = conn.execute("SELECT currentness_state, retrieval_eligible FROM search_document WHERE search_document_id='doc-01'").fetchone()
        self.assertEqual(sd_row[0], "historical")
        self.assertEqual(sd_row[1], 0)
        conn.close()

    def test_prune_superseded_search_documents_and_sidecars(self) -> None:
        """Verify pruning removes or de-indexes historical docs and deletes sidecar vectors."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO search_document (search_document_id, currentness_state, retrieval_eligible) VALUES ('doc-stale', 'historical', 0)")
        conn.execute("INSERT INTO dense_vector (search_document_id, vector) VALUES ('doc-stale', X'0102')")
        conn.commit()

        res = prune_superseded_search_documents(conn, prune_sidecars=True, purge_deleted=True)
        self.assertEqual(res["search_documents_pruned"], 1)
        self.assertEqual(res["sidecars_pruned"].get("dense_vector"), 1)

        # Confirm dense_vector row is deleted
        cnt = conn.execute("SELECT count(*) FROM dense_vector WHERE search_document_id='doc-stale'").fetchone()[0]
        self.assertEqual(cnt, 0)
        conn.close()

    def test_calculate_graph_health_metrics_and_markdown(self) -> None:
        """Verify bloat ratio calculation, orphan evidence detection, and report rendering."""
        conn = sqlite3.connect(self.db_path)
        # 1 active node
        conn.execute("INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n1', 'Node 1', 'entity')")
        # 1 active, 2 historical assertions -> 3 total, 1 active -> 3.0x bloat ratio (>1.5)
        conn.execute("INSERT INTO assertion (assertion_id, currentness_state) VALUES ('a1', 'current')")
        conn.execute("INSERT INTO assertion (assertion_id, currentness_state) VALUES ('a2', 'historical')")
        conn.execute("INSERT INTO assertion (assertion_id, currentness_state) VALUES ('a3', 'historical')")
        # 1 orphaned evidence record
        conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id) VALUES ('nonexistent_ast', 'ev1')")
        conn.commit()

        metrics = calculate_graph_health_metrics(conn)
        self.assertEqual(metrics["status"], "PASS")
        self.assertTrue(metrics["needs_gc"])
        self.assertEqual(metrics["recommendation"], "RUN_GRAPH_GC")
        self.assertEqual(metrics["counts"]["orphaned_evidence_records"], 1)
        self.assertEqual(metrics["assertion_bloat_ratio"], 3.0)

        md = render_health_report_markdown(metrics, target_graph=str(self.db_path))
        self.assertIn("# Knowledge Graph Health & Hygiene Audit", md)
        self.assertIn("RUN_GRAPH_GC", md)
        self.assertIn("3.0x", md)
        conn.close()

    def test_cli_parser_and_execution_backtest_health(self) -> None:
        """Verify backtest-health CLI command parses and executes cleanly."""
        parser = build_parser()
        args = parser.parse_args([
            "backtest-health",
            "--graph", str(self.db_path),
            "--format", "json",
            "--recommend-gc",
        ])
        self.assertEqual(args.command, "backtest-health")
        self.assertEqual(args.format, "json")

        metrics = _backtest_health(args)
        self.assertEqual(metrics["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
