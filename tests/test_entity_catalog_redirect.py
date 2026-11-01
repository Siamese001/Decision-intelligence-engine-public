from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.identity_core.catalog import GraphEntityCatalog


class TestEntityCatalogRedirect(unittest.TestCase):
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

        # Active canonical entity
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("org_nee", "org", "Apex Global Enterprises / NEE", "Apex Global Enterprises", "Parent org", "active", 1, "2026-01-01", "2026-01-01")
        )
        # Merged entity (inactive)
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("entity-c0218ba670e8", "org", "Apex Energy Transmission", "APEX_TRANS", "Transmission sub", "merged", 0, "2026-01-01", "2026-01-02")
        )
        # Active redirect: entity-c0218ba670e8 -> org_nee
        con.execute(
            "INSERT INTO entity_redirect VALUES (?, ?, ?, ?, ?)",
            ("entity-c0218ba670e8", "org_nee", "merge-1", 1, "2026-01-02")
        )
        con.commit()
        con.close()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_redirect_resolves_merged_node_name_to_target_entity(self) -> None:
        catalog = GraphEntityCatalog(self.db_path)
        # Merged node's canonical name resolves to org_nee
        matches, basis = catalog.exact("Apex Energy Transmission", None)
        self.assertEqual(["org_nee"], matches)
        self.assertEqual("exact_normalized_name", basis)

    def test_redirect_resolves_merged_node_id_to_target_entity(self) -> None:
        catalog = GraphEntityCatalog(self.db_path)
        # Merged node ID as stable_identifier resolves to org_nee
        matches, basis = catalog.exact("Unknown Reported Name", "entity-c0218ba670e8")
        self.assertEqual(["org_nee"], matches)
        self.assertEqual("stable_identifier", basis)

    def test_redirect_resolves_acronym_to_target_entity(self) -> None:
        catalog = GraphEntityCatalog(self.db_path)
        # Acronym 'apex_trans' resolves to org_nee
        matches, basis = catalog.exact("APEX_TRANS", None)
        self.assertEqual(["org_nee"], matches)
        self.assertIn(basis, ("exact_normalized_name", "governed_alias"))

    def test_inactive_redirect_is_ignored(self) -> None:
        con = sqlite3.connect(self.db_path)
        # Inactive merged entity with inactive redirect
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("entity-inactive", "org", "Inactive Subsidiary", "", "", "merged", 0, "2026-01-01", "2026-01-02")
        )
        con.execute(
            "INSERT INTO entity_redirect VALUES (?, ?, ?, ?, ?)",
            ("entity-inactive", "org_nee", "merge-inactive", 0, "2026-01-02")
        )
        con.commit()
        con.close()

        catalog = GraphEntityCatalog(self.db_path)
        matches, _ = catalog.exact("Inactive Subsidiary", None)
        self.assertEqual([], matches)

    def test_multihop_redirect_resolves_to_terminal_active_node(self) -> None:
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("entity-hop1", "org", "Hop 1 Intermediate", "", "", "merged", 0, "2026-01-01", "2026-01-02")
        )
        con.execute(
            "INSERT INTO node VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("entity-hop2", "org", "Hop 2 Origin", "", "", "merged", 0, "2026-01-01", "2026-01-02")
        )
        # hop2 -> hop1 -> org_nee
        con.execute(
            "INSERT INTO entity_redirect VALUES (?, ?, ?, ?, ?)",
            ("entity-hop2", "entity-hop1", "merge-h2", 1, "2026-01-02")
        )
        con.execute(
            "INSERT INTO entity_redirect VALUES (?, ?, ?, ?, ?)",
            ("entity-hop1", "org_nee", "merge-h1", 1, "2026-01-02")
        )
        con.commit()
        con.close()

        catalog = GraphEntityCatalog(self.db_path)
        matches, _ = catalog.exact("Hop 2 Origin", None)
        self.assertEqual(["org_nee"], matches)


if __name__ == "__main__":
    unittest.main()
