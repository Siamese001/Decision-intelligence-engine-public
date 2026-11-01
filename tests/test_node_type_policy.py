"""Unit tests for the registry-backed node type policy.

These tests verify the node_type_policy module's closed-world controls
against the live graph's node_type_registry.  They are deterministic,
read-only, and require no embedding backend.
"""
from __future__ import annotations

import unittest

from dual_pipeline.core import ROOT, connect_sqlite
from dual_pipeline.node_type_policy import (
    NO_NAME_AUTO_MERGE_RULE,
    NON_CREATABLE_NODE_TYPES,
    active_creatable_node_types,
    active_creatable_node_types_in_connection,
    registry_identity_rule,
    require_creatable_node_type,
    type_forbids_name_auto_merge,
)

GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class NodeTypePolicyTests(unittest.TestCase):
    """Policy enforcement tests against the live graph registry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    # --- NON_CREATABLE_NODE_TYPES enforcement ---

    def test_non_creatable_types_are_rejected(self) -> None:
        for node_type in NON_CREATABLE_NODE_TYPES:
            with self.subTest(node_type=node_type), self.assertRaises(ValueError):
                require_creatable_node_type(self.connection, node_type)

    def test_deprecated_prefixed_types_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            require_creatable_node_type(self.connection, "deprecated_anything")

    # --- Empty / invalid input ---

    def test_empty_string_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            require_creatable_node_type(self.connection, "")

    def test_whitespace_only_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            require_creatable_node_type(self.connection, "   ")

    def test_none_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            require_creatable_node_type(self.connection, None)

    def test_integer_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            require_creatable_node_type(self.connection, 42)

    # --- Unregistered and inactive types ---

    def test_unregistered_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            require_creatable_node_type(self.connection, "nonexistent_type_xyzzy")

    # --- Active creatable types ---

    def test_active_creatable_types_is_nonempty(self) -> None:
        types = active_creatable_node_types(GRAPH)
        self.assertGreater(len(types), 0, "No active creatable types found")

    def test_active_creatable_types_match_connection_variant(self) -> None:
        from_path = active_creatable_node_types(GRAPH)
        from_conn = active_creatable_node_types_in_connection(self.connection)
        self.assertEqual(from_path, from_conn)

    def test_active_creatable_types_exclude_non_creatable(self) -> None:
        types = set(active_creatable_node_types(GRAPH))
        overlap = types & NON_CREATABLE_NODE_TYPES
        self.assertEqual(set(), overlap, f"Non-creatable types in active list: {overlap}")

    def test_active_creatable_types_have_no_name_auto_merge_rule(self) -> None:
        types = active_creatable_node_types(GRAPH)
        for node_type in types:
            with self.subTest(node_type=node_type):
                rule = registry_identity_rule(GRAPH, node_type)
                self.assertIsNotNone(rule)
                self.assertIn(
                    NO_NAME_AUTO_MERGE_RULE,
                    rule.casefold(),
                    f"{node_type} lacks no-name-auto-merge rule",
                )

    def test_require_creatable_accepts_every_active_creatable_type(self) -> None:
        types = active_creatable_node_types(GRAPH)
        for node_type in types:
            with self.subTest(node_type=node_type):
                result = require_creatable_node_type(self.connection, node_type)
                self.assertEqual(node_type, result)

    # --- type_forbids_name_auto_merge consistency ---

    def test_type_forbids_name_auto_merge_is_true_for_creatable_types(self) -> None:
        types = active_creatable_node_types(GRAPH)
        for node_type in types:
            with self.subTest(node_type=node_type):
                self.assertTrue(
                    type_forbids_name_auto_merge(GRAPH, node_type),
                    f"{node_type} should forbid name auto-merge",
                )

    # --- Stable superset check ---

    def test_every_used_node_type_is_registered(self) -> None:
        """Every node_type in the node table must exist in the registry."""
        used_types = {
            row[0]
            for row in self.connection.execute(
                "SELECT DISTINCT node_type FROM node"
            )
        }
        registered_types = {
            row[0]
            for row in self.connection.execute(
                "SELECT node_type_id FROM node_type_registry"
            )
        }
        unregistered = used_types - registered_types
        self.assertEqual(
            set(),
            unregistered,
            f"Node types used but not registered: {unregistered}",
        )

    def test_active_creatable_types_are_sorted(self) -> None:
        types = active_creatable_node_types(GRAPH)
        self.assertEqual(types, sorted(types))


if __name__ == "__main__":
    unittest.main(verbosity=2)
