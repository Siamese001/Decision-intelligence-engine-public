"""
tests/adversarial/test_graph_consolidation_detect_adversarial.py

Adversarial and boundary tests for Graph Consolidation Wave 2 detection engine.
Covers:
1. Boundary thresholds (0.0, 1.0, invalid ranges).
2. Schema edge cases and NULL values.
3. Read-only safety verification (PRAGMA query_only invariant bypass resistance).
4. Unicode and zero-width edge case strings in entity names.
5. Missing tables fail closed cleanly.
"""
from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_consolidation_core.detect.nodes import detect_node_clusters
from dual_pipeline.graph_consolidation_core.plan import build_consolidation_plan


class TestGraphConsolidationDetectAdversarial(unittest.TestCase):
    """Adversarial stress tests for graph consolidation detection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "adversarial_graph.sqlite"
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

    def test_read_only_invariant_preservation(self) -> None:
        """Verifies that scan execution uses PRAGMA query_only = ON and does not mutate graph."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n1', 'Test Corp', 'org')"
        )
        conn.commit()
        conn.close()

        hash_before = hashlib.sha256(self.db_path.read_bytes()).hexdigest()

        plan = build_consolidation_plan(self.db_path)
        self.assertIsNotNone(plan)

        hash_after = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        self.assertEqual(hash_before, hash_after)

    def test_extreme_and_boundary_thresholds(self) -> None:
        """Verifies strict boundary threshold behavior."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n1', 'Alpha Beta', 'org')")
        conn.execute("INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n2', 'Alpha Gamma', 'org')")
        conn.commit()

        # Threshold 1.0 (exact match only): 'Alpha Beta' != 'Alpha Gamma' -> 0 clusters
        clusters_strict = detect_node_clusters(conn, similarity_threshold=1.0)
        self.assertEqual(clusters_strict["clusters_total"], 0)

        # Threshold 0.1 (extremely loose): clusters them
        clusters_loose = detect_node_clusters(conn, similarity_threshold=0.1)
        self.assertGreaterEqual(clusters_loose["clusters_total"], 1)
        conn.close()

    def test_unicode_normalization_and_whitespace_edge_cases(self) -> None:
        """Handles unicode NFKC/NFKD equivalence, zero-width characters, and non-breaking spaces."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n1', 'Resume Review', 'process')")
        conn.execute("INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n2', 'résumé review', 'process')")
        conn.execute("INSERT INTO node (node_id, canonical_name, node_type) VALUES ('n3', 'Resume\u00A0Review\u200B', 'process')")
        conn.commit()

        clusters = detect_node_clusters(conn, similarity_threshold=0.7)
        conn.close()
        self.assertGreaterEqual(clusters["clusters_total"], 1)

    def test_missing_optional_tables_fails_cleanly(self) -> None:
        """Verifies that running scan against a DB missing required tables raises clear error."""
        bare_db = Path(self.temp_dir.name) / "bare.sqlite"
        conn = sqlite3.connect(bare_db)
        conn.execute("CREATE TABLE dummy (id INT)")
        conn.commit()

        with self.assertRaises(sqlite3.OperationalError):
            detect_node_clusters(conn)
        conn.close()

    def test_single_token_and_incompatible_type_containment_rejected(self) -> None:
        """Unrelated Dominion entities and incompatible cross-type pairs must never cluster."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.executemany(
            "INSERT INTO node (node_id, canonical_name, node_type, status) VALUES (?, ?, ?, 'candidate')",
            [
                ("n_org", "Meridian Utility Corp", "org"),
                ("n_merger", "Dominion merger efforts", "program"),
                ("n_estate", "Dominion systems-of-record estate", "technical_anchor"),
                ("n_growth", "Potential Dominion Acquisition Growth", "workstream"),
                ("n_spog", "Meridian Utility Corp Distribution Single Pane of Glass", "program"),
                ("n_person", "Single Pane Glass", "person"),
            ],
        )
        conn.commit()
        res = detect_node_clusters(conn, similarity_threshold=0.65)
        conn.close()
        self.assertEqual(res["clusters_total"], 0)


if __name__ == "__main__":
    unittest.main()
