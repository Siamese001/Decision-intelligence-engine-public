"""Unit tests for scoped Graph Scout duplicate detection (scan_new_nodes)."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_scout import GraphScout


def _init_test_graph(db_path: Path) -> None:
    """Initialize a mock SQLite knowledge graph with known clusters."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    cursor.execute(
        """CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            intentional_orphan INTEGER DEFAULT 0
        )"""
    )

    cursor.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            validation_state TEXT,
            confidence_score REAL,
            valid_from TEXT,
            recorded_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    # Cluster 1: project
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:nexus_core', 'project', 'Project Nexus Core Architecture', 'Nexus Core',
            'Core telemetry platform', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:nexus_core_platform', 'project', 'Project Nexus Core Platform Architecture', 'Nexus Core Platform',
            'Core telemetry platform initiative', 'active', 1, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z'
        )"""
    )

    # Cluster 2: system
    cursor.execute(
        """INSERT INTO node VALUES (
            'system:fpl_scada', 'system', 'APEX Grid SCADA Core', 'SCADA Core',
            'Grid operations network', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    cursor.execute(
        """INSERT INTO node VALUES (
            'system:fpl_scada_ops', 'system', 'APEX Grid SCADA Operational Core', 'SCADA Ops',
            'Grid operations network telemetry', 'active', 1, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z'
        )"""
    )

    # Unrelated person
    cursor.execute(
        """INSERT INTO node VALUES (
            'person:amit_ayer', 'person', 'Amit Ayer', 'Amit Ayer',
            'Lead Enterprise Architect', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )

    conn.commit()
    conn.close()


class TestScopedGraphScout(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.graph_path = Path(self.temp_dir.name) / "test_graph.sqlite"
        _init_test_graph(self.graph_path)
        self.scout = GraphScout(self.graph_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_scan_new_nodes_targets_only_specified_entity(self) -> None:
        with self.scout._connect_ro() as conn:
            # Scoped to only newly introduced project node
            findings = self.scout.scan_new_nodes(["project:nexus_core_platform"], conn)

            # Should find (nexus_core, nexus_core_platform)
            self.assertEqual(len(findings), 1)
            f = findings[0]
            self.assertEqual(f.node_type_a, "project")
            self.assertIn("nexus_core", f.node_id_a)
            self.assertIn("nexus_core_platform", f.node_id_b)

            # Should NOT find system cluster because system nodes were not in target list
            system_findings = [x for x in findings if x.node_type_a == "system"]
            self.assertEqual(len(system_findings), 0)

    def test_scan_new_nodes_empty_target_returns_empty(self) -> None:
        with self.scout._connect_ro() as conn:
            findings = self.scout.scan_new_nodes([], conn)
            self.assertEqual(len(findings), 0)

    def test_scan_new_nodes_unrelated_target_returns_empty(self) -> None:
        with self.scout._connect_ro() as conn:
            findings = self.scout.scan_new_nodes(["person:amit_ayer"], conn)
            self.assertEqual(len(findings), 0)

    def test_scan_new_nodes_enforces_query_only(self) -> None:
        with self.scout._connect_ro() as conn:
            # Verify PRAGMA query_only is ON
            row = conn.execute("PRAGMA query_only;").fetchone()
            self.assertEqual(row[0], 1)

            # Mutation attempt should fail
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM node WHERE node_id = 'person:amit_ayer'")


if __name__ == "__main__":
    unittest.main()
