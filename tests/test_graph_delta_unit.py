"""Unit tests for graph delta verification logic.

These tests exercise the deterministic, provider-free internals of
graph_delta.py against the live graph database.  They require no
embedding backend.
"""
from __future__ import annotations

import unittest
from typing import Any

from dual_pipeline.core import ROOT, connect_sqlite, sha256_file
from dual_pipeline.graph_delta import (
    _COMPONENTS,
    _graph_counts,
    _planned_increments,
    _row,
)

GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class GraphCountsTests(unittest.TestCase):
    """Verify _graph_counts returns accurate governed component counts."""

    def test_all_components_are_represented(self) -> None:
        counts = _graph_counts(GRAPH)
        expected_keys = {component for component, _label, _table in _COMPONENTS}
        self.assertEqual(expected_keys, set(counts.keys()))

    def test_counts_are_non_negative_integers(self) -> None:
        counts = _graph_counts(GRAPH)
        for key, value in counts.items():
            with self.subTest(key=key):
                self.assertIsInstance(value, int)
                self.assertGreaterEqual(value, 0)

    def test_counts_match_direct_sql(self) -> None:
        """Cross-check _graph_counts against manual queries."""
        counts = _graph_counts(GRAPH)
        connection = connect_sqlite(GRAPH, readonly=True)
        try:
            self.assertEqual(
                counts["source"],
                connection.execute("SELECT count(*) FROM source").fetchone()[0],
            )
            self.assertEqual(
                counts["source_version"],
                connection.execute("SELECT count(*) FROM source_version").fetchone()[0],
            )
            self.assertEqual(
                counts["evidence"],
                connection.execute("SELECT count(*) FROM evidence_item").fetchone()[0],
            )
            self.assertEqual(
                counts["audit_event"],
                connection.execute("SELECT count(*) FROM audit_event").fetchone()[0],
            )
        finally:
            connection.close()

    def test_graph_counts_is_read_only(self) -> None:
        before = sha256_file(GRAPH)
        _graph_counts(GRAPH)
        self.assertEqual(before, sha256_file(GRAPH))


class PlannedIncrementsTests(unittest.TestCase):
    """Verify _planned_increments for edge cases."""

    def test_none_change_set_returns_all_zeros(self) -> None:
        increments = _planned_increments(None, GRAPH)
        for key, value in increments.items():
            with self.subTest(key=key):
                self.assertEqual(0, value)

    def test_missing_operations_key_raises(self) -> None:
        bad_set: dict[str, Any] = {"extensions": {}}
        with self.assertRaises(ValueError):
            _planned_increments(bad_set, GRAPH)

    def test_missing_source_extension_raises(self) -> None:
        bad_set: dict[str, Any] = {
            "operations": [],
            "extensions": {"com.partner_advisory.nee.operation-payloads": {}},
        }
        with self.assertRaises(ValueError):
            _planned_increments(bad_set, GRAPH)

    def test_duplicate_evidence_ids_raise(self) -> None:
        bad_set: dict[str, Any] = {
            "operations": [],
            "extensions": {
                "com.partner_advisory.nee.source": {
                    "source_id": "src-test-dup",
                    "source_version_id": "sv-test-dup",
                    "evidence": [
                        {"evidence_id": "ev-1"},
                        {"evidence_id": "ev-1"},
                    ],
                },
                "com.partner_advisory.nee.operation-payloads": {},
            },
        }
        with self.assertRaises(ValueError):
            _planned_increments(bad_set, GRAPH)

    def test_empty_evidence_id_raises(self) -> None:
        bad_set: dict[str, Any] = {
            "operations": [],
            "extensions": {
                "com.partner_advisory.nee.source": {
                    "source_id": "src-test-empty-ev",
                    "source_version_id": "sv-test-empty-ev",
                    "evidence": [
                        {"evidence_id": ""},
                    ],
                },
                "com.partner_advisory.nee.operation-payloads": {},
            },
        }
        with self.assertRaises(ValueError):
            _planned_increments(bad_set, GRAPH)


class DeltaRowFormattingTests(unittest.TestCase):
    """Verify the _row helper produces valid delta table entries."""

    def test_positive_delta_describes_inserts(self) -> None:
        row = _row(
            component_key="source",
            graph_component="Sources",
            baseline=100,
            planned_delta=3,
        )
        self.assertEqual("source", row["component_key"])
        self.assertEqual(103, row["planned_after"])
        self.assertEqual("+3", row["delta"])
        self.assertIn("insert", row["change"].lower())

    def test_zero_delta_describes_no_insert(self) -> None:
        row = _row(
            component_key="audit_event",
            graph_component="Graph audit events",
            baseline=9,
            planned_delta=0,
        )
        self.assertEqual(9, row["planned_after"])
        self.assertEqual("+0", row["delta"])
        self.assertIn("no governed insert", row["change"].lower())

    def test_row_preserves_component_key_and_label(self) -> None:
        row = _row(
            component_key="evidence",
            graph_component="Evidence items",
            baseline=50,
            planned_delta=5,
        )
        self.assertEqual("evidence", row["component_key"])
        self.assertEqual("Evidence items", row["graph_component"])


class ComponentRegistryTests(unittest.TestCase):
    """Verify _COMPONENTS is well-formed and covers expected tables."""

    def test_component_keys_are_unique(self) -> None:
        keys = [component for component, _label, _table in _COMPONENTS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_expected_components_are_present(self) -> None:
        keys = {component for component, _label, _table in _COMPONENTS}
        required = {
            "source", "source_version", "evidence",
            "external_concept", "internal_entity",
            "candidate_assertion", "canonical_assertion",
            "superseded_assertion",
            "candidate_search_document", "audit_event",
        }
        self.assertEqual(required, keys)

    def test_component_labels_are_nonempty(self) -> None:
        for _component, label, _table in _COMPONENTS:
            self.assertTrue(label.strip(), "Empty label in _COMPONENTS")


if __name__ == "__main__":
    unittest.main(verbosity=2)
