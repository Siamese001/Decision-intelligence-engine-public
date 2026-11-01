from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.identity_core.catalog import GraphEntityCatalog


class TestEntityCatalogRedirectAdversarial(unittest.TestCase):
    """Adversarial and boundary tests covering cycle detection, missing tables, and dead-end redirects."""

    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.db_path = Path(self.tmp.name)
        con = sqlite3.connect(self.db_path)
        con.execute(
            """CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                display_name TEXT,
                description TEXT,
                status TEXT,
                active INTEGER,
                created_at TEXT,
                updated_at TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE canonical_alias (
                canonical_alias_id TEXT PRIMARY KEY,
                normalized_alias TEXT,
                display_alias TEXT,
                entity_id TEXT,
                alias_type TEXT,
                source_id TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                valid_from TEXT,
                valid_to TEXT,
                ambiguity_state TEXT,
                preferred_flag INTEGER,
                review_state TEXT,
                active INTEGER,
                created_at TEXT
            )"""
        )
        con.execute(
            """CREATE TABLE entity_redirect (
                from_node_id TEXT PRIMARY KEY,
                to_node_id TEXT,
                merge_event_id TEXT,
                active INTEGER,
                created_at TEXT
            )"""
        )
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("org_primary", "org", "Primary Organization", "Primary", "Desc", "active", 1, "2026-01-01", "2026-01-01")
        )
        con.commit()
        con.close()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_cycle_redirect_does_not_infinite_loop(self) -> None:
        """Cycle A -> B -> A must terminate gracefully and not crash or loop forever."""
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO entity_redirect VALUES ('cycle_a', 'cycle_b', 'm1', 1, '2026-01-01')")
        con.execute("INSERT INTO entity_redirect VALUES ('cycle_b', 'cycle_a', 'm2', 1, '2026-01-01')")
        con.commit()
        con.close()

        # Must initialize without RecursionError or hanging
        catalog = GraphEntityCatalog(self.db_path)
        matches, _ = catalog.exact("Cycle Candidate", "cycle_a")
        self.assertEqual([], matches)

    def test_dead_end_redirect_to_nonexistent_node(self) -> None:
        """Redirect pointing to a non-existent or inactive terminal node must be dropped."""
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO entity_redirect VALUES ('ghost_from', 'ghost_to', 'm3', 1, '2026-01-01')")
        con.commit()
        con.close()

        catalog = GraphEntityCatalog(self.db_path)
        matches, _ = catalog.exact("Ghost", "ghost_from")
        self.assertEqual([], matches)

    def test_empty_or_whitespace_node_names_do_not_crash(self) -> None:
        """Merged node with None or whitespace names must be safely skipped."""
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("blank_node", "org", "", None, "", "merged", 0, "2026-01-01", "2026-01-01")
        )
        con.execute("INSERT INTO entity_redirect VALUES ('blank_node', 'org_primary', 'm4', 1, '2026-01-01')")
        con.commit()
        con.close()

        catalog = GraphEntityCatalog(self.db_path)
        matches, basis = catalog.exact("Primary Organization", None)
        self.assertEqual(["org_primary"], matches)

    def test_missing_entity_redirect_table(self) -> None:
        """Graph DB without entity_redirect table must initialize normally."""
        con = sqlite3.connect(self.db_path)
        con.execute("DROP TABLE entity_redirect")
        con.commit()
        con.close()

        catalog = GraphEntityCatalog(self.db_path)
        matches, basis = catalog.exact("Primary Organization", None)
        self.assertEqual(["org_primary"], matches)


if __name__ == "__main__":
    unittest.main()
