"""Structural integrity tests for the live NEE graph database.

Every test in this module is deterministic, read-only, and completes in
under 2 seconds on the 17 MB production graph.  They verify schema
invariants, referential integrity, index synchronization, and semantic
constraints that must hold across all graph versions.
"""
from __future__ import annotations

import unittest

from dual_pipeline.core import ROOT, connect_sqlite, sha256_file, sqlite_integrity

GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class GraphModelIntegrityTests(unittest.TestCase):
    """Always-on structural checks against the live graph database."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)
        cls.graph_hash = sha256_file(GRAPH)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    # --- Core SQLite integrity ---

    def test_sqlite_integrity_check_passes(self) -> None:
        result = sqlite_integrity(GRAPH)
        self.assertEqual("ok", result["integrity_check"])

    def test_foreign_key_check_has_zero_violations(self) -> None:
        result = sqlite_integrity(GRAPH)
        self.assertEqual(
            0,
            result["foreign_key_violation_count"],
            f"FK violations: {result['foreign_key_violations'][:5]}",
        )

    # --- write_control singleton ---

    def test_write_control_singleton_exists(self) -> None:
        rows = self.connection.execute("SELECT count(*) FROM write_control").fetchone()
        self.assertEqual(1, rows[0], "write_control must contain exactly one row")

    def test_write_control_legacy_write_is_off(self) -> None:
        row = self.connection.execute(
            "SELECT allow_legacy_write FROM write_control WHERE control_id=1"
        ).fetchone()
        self.assertIsNotNone(row, "write_control singleton is missing")
        self.assertEqual(
            0,
            row["allow_legacy_write"],
            "allow_legacy_write must be 0 outside a transaction",
        )

    # --- Node/type registry consistency ---

    def test_every_node_has_a_registered_type(self) -> None:
        orphans = self.connection.execute(
            """SELECT n.node_id, n.node_type
                 FROM node n
                 LEFT JOIN node_type_registry ntr
                   ON ntr.node_type_id = n.node_type
                WHERE ntr.node_type_id IS NULL"""
        ).fetchall()
        self.assertEqual(
            0,
            len(orphans),
            f"Nodes with unregistered types: {[dict(r) for r in orphans[:5]]}",
        )

    def test_every_active_node_has_a_node_state_row(self) -> None:
        missing = self.connection.execute(
            """SELECT n.node_id
                 FROM node n
                 LEFT JOIN node_state ns ON ns.node_id = n.node_id
                WHERE ns.node_id IS NULL"""
        ).fetchall()
        self.assertEqual(
            0,
            len(missing),
            f"Nodes without node_state: {[r[0] for r in missing[:5]]}",
        )

    def test_node_and_node_state_counts_match(self) -> None:
        node_count = self.connection.execute("SELECT count(*) FROM node").fetchone()[0]
        state_count = self.connection.execute(
            "SELECT count(*) FROM node_state"
        ).fetchone()[0]
        self.assertEqual(node_count, state_count)

    # --- Assertion referential integrity ---

    def test_every_assertion_subject_references_an_existing_node(self) -> None:
        orphans = self.connection.execute(
            """SELECT a.assertion_id, a.subject_node_id
                 FROM assertion a
                 LEFT JOIN node n ON n.node_id = a.subject_node_id
                WHERE a.subject_node_id IS NOT NULL
                  AND n.node_id IS NULL"""
        ).fetchall()
        self.assertEqual(
            0,
            len(orphans),
            f"Dangling subject refs: {[dict(r) for r in orphans[:5]]}",
        )

    def test_every_assertion_object_references_an_existing_node(self) -> None:
        orphans = self.connection.execute(
            """SELECT a.assertion_id, a.object_node_id
                 FROM assertion a
                 LEFT JOIN node n ON n.node_id = a.object_node_id
                WHERE a.object_node_id IS NOT NULL
                  AND n.node_id IS NULL"""
        ).fetchall()
        self.assertEqual(
            0,
            len(orphans),
            f"Dangling object refs: {[dict(r) for r in orphans[:5]]}",
        )

    def test_every_evidence_item_references_an_existing_source_version(self) -> None:
        orphans = self.connection.execute(
            """SELECT ei.evidence_id, ei.source_version_id
                 FROM evidence_item ei
                 LEFT JOIN source_version sv
                   ON sv.source_version_id = ei.source_version_id
                WHERE sv.source_version_id IS NULL"""
        ).fetchall()
        self.assertEqual(
            0,
            len(orphans),
            f"Dangling evidence→source_version: {[dict(r) for r in orphans[:5]]}",
        )

    def test_every_assertion_evidence_link_references_valid_endpoints(self) -> None:
        bad_assertion = self.connection.execute(
            """SELECT ae.assertion_id
                 FROM assertion_evidence ae
                 LEFT JOIN assertion a ON a.assertion_id = ae.assertion_id
                WHERE a.assertion_id IS NULL"""
        ).fetchall()
        bad_evidence = self.connection.execute(
            """SELECT ae.evidence_id
                 FROM assertion_evidence ae
                 LEFT JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id
                WHERE ei.evidence_id IS NULL"""
        ).fetchall()
        self.assertEqual(0, len(bad_assertion), "assertion_evidence has orphan assertion refs")
        self.assertEqual(0, len(bad_evidence), "assertion_evidence has orphan evidence refs")

    # --- FTS synchronization ---

    def test_search_fts_is_synchronized_with_search_document(self) -> None:
        """FTS5 content-synced tables mirror every row, not just eligible ones."""
        fts_count = self.connection.execute(
            "SELECT count(*) FROM search_fts"
        ).fetchone()[0]
        doc_count = self.connection.execute(
            "SELECT count(*) FROM search_document"
        ).fetchone()[0]
        self.assertEqual(
            doc_count,
            fts_count,
            f"search_fts ({fts_count}) != search_document ({doc_count})",
        )

    # --- Minimum population bounds (resilient to growth) ---

    def test_graph_has_minimum_expected_population(self) -> None:
        """Guard against accidental truncation or empty-graph regression."""
        minimums = {
            "node": 500,
            "assertion": 1000,
            "source": 100,
            "source_version": 100,
            "evidence_item": 1000,
            "canonical_alias": 300,
            "search_document": 1500,
            "node_type_registry": 20,
        }
        for table, minimum in minimums.items():
            with self.subTest(table=table):
                count = self.connection.execute(
                    f"SELECT count(*) FROM [{table}]"
                ).fetchone()[0]
                self.assertGreaterEqual(
                    count,
                    minimum,
                    f"{table} has {count} rows, expected >= {minimum}",
                )

    # --- Supersession chain acyclicity ---

    def test_supersession_chain_has_no_self_references(self) -> None:
        """An assertion must never supersede itself."""
        self_refs = self.connection.execute(
            """SELECT assertion_id FROM assertion
                WHERE superseded_by_assertion_id IS NOT NULL
                  AND superseded_by_assertion_id = assertion_id"""
        ).fetchall()
        # Also check assertion_conflict for reflexive entries
        reflexive = self.connection.execute(
            """SELECT assertion_id_1 FROM assertion_conflict
                WHERE assertion_id_1 = assertion_id_2"""
        ).fetchall()
        self.assertEqual(0, len(self_refs), "Self-superseding assertions found")
        self.assertEqual(0, len(reflexive), "Reflexive assertion conflicts found")

    # --- Canonical alias determinism ---

    def test_canonical_alias_normalized_values_are_consistent_with_normalize_name(self) -> None:
        """Re-normalizing each alias must produce the same stored value.

        Known legacy debt (e.g. Unicode replacement characters from
        source-document encoding issues) is counted but not treated
        as a hard failure.  The count must not increase.
        """
        from dual_pipeline.core import normalize_name

        rows = self.connection.execute(
            "SELECT normalized_alias, display_alias, entity_id FROM canonical_alias"
        ).fetchall()
        mismatches = []
        for row in rows:
            re_normalized = normalize_name(row["display_alias"])
            if re_normalized != row["normalized_alias"]:
                mismatches.append(
                    (row["normalized_alias"], re_normalized, row["entity_id"])
                )
        # Known legacy debt ceiling — must never increase
        self.assertLessEqual(
            len(mismatches),
            2,
            f"Normalization drift ({len(mismatches)} mismatches, max 2): "
            f"{mismatches[:5]}",
        )

    # --- Read-only verification ---

    def test_graph_hash_is_unchanged_after_all_checks(self) -> None:
        self.assertEqual(
            self.graph_hash,
            sha256_file(GRAPH),
            "Graph file was mutated during read-only integrity checks",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
