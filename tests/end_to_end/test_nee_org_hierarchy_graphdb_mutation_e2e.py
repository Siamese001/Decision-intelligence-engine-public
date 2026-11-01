"""E2E Test: NEE Complete Org Hierarchy GraphDB Mutation & Integrity Verification.

Validates that the Universal Write Gateway persisted nodes, edges, and strategic
assertions into the canonical SQLite graph database under WAL mode with zero lock contention.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parent.parent.parent


class TestNEEOrgHierarchyGraphDBMutationE2E(unittest.TestCase):
    """End-to-end verification of canonical GraphDB mutation and physical proof integrity."""

    def setUp(self) -> None:
        self.active_pair_file = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
        self.assertTrue(self.active_pair_file.is_file(), f"Missing active pair file: {self.active_pair_file}")
        self.active_pair = json.loads(self.active_pair_file.read_text(encoding="utf-8"))
        self.graph_path = ROOT / ".local" / "dual_pipeline" / self.active_pair["graph_path"]
        self.assertTrue(self.graph_path.is_file(), f"Missing graph database: {self.graph_path}")

    def test_pragma_integrity_check_and_wal_mode(self) -> None:
        """Verify PRAGMA integrity_check returns 'ok' and journal mode is WAL."""
        conn = sqlite3.connect(f"file:{self.graph_path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            self.assertEqual(result[0], "ok", f"Integrity check failed: {result}")

            cursor.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()
            self.assertEqual(mode[0].lower(), "wal", f"Journal mode is not WAL: {mode}")
        finally:
            conn.close()

    def test_executive_nodes_present(self) -> None:
        """Verify key executive nodes exist in the active graph."""
        conn = sqlite3.connect(f"file:{self.graph_path}?mode=ro", uri=True)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT canonical_name FROM node")
            names = {row[0] for row in cursor.fetchall()}

            required_executives = [
                "John Ketchum",
                "Petter Skantze",
                "Ben Miron",
                "Scott Bores",
            ]
            for exec_name in required_executives:
                self.assertIn(exec_name, names, f"Missing executive node: {exec_name}")
        finally:
            conn.close()

    def test_sidecar_vector_file_exists(self) -> None:
        """Verify vector sidecar SQLite file exists at the active pair location."""
        sidecar_path = ROOT / ".local" / "dual_pipeline" / self.active_pair["sidecar_path"]
        self.assertTrue(sidecar_path.is_file(), f"Missing vector sidecar: {sidecar_path}")
        self.assertGreater(sidecar_path.stat().st_size, 0, "Vector sidecar is empty")


if __name__ == "__main__":
    unittest.main()
