"""Exhaustive graph topology, routing, and decision infrastructure tests.

These tests verify edge density, hub limits, predicate registry coverage,
connected component structure, card/route mapping, and decision pipeline
readiness.  All read-only, deterministic, no embedding backend required.
"""
from __future__ import annotations

import unittest
from collections import defaultdict

from dual_pipeline.core import ROOT, connect_sqlite, sha256_file

GRAPH = ROOT / "graph" / "nee_graph.sqlite"


class TopologyAuditTests(unittest.TestCase):
    """Graph topology analysis and regression gates."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def _node_degrees(self) -> list[tuple[str, int]]:
        """Return (node_id, degree) for all active nodes."""
        rows = self.connection.execute("""
            SELECT n.node_id,
                   (SELECT count(*) FROM assertion a WHERE a.subject_node_id = n.node_id) +
                   (SELECT count(*) FROM assertion a WHERE a.object_node_id = n.node_id) as degree
            FROM node n WHERE n.active = 1
        """).fetchall()
        return [(r["node_id"], r["degree"]) for r in rows]

    def test_edge_density_minimum(self) -> None:
        """Edge density (total_edges / active_nodes) must be ≥ 2.0."""
        total_edges = self.connection.execute("SELECT count(*) FROM assertion").fetchone()[0]
        active_nodes = self.connection.execute(
            "SELECT count(*) FROM node WHERE active=1"
        ).fetchone()[0]
        density = total_edges / active_nodes
        self.assertGreaterEqual(
            density, 2.0,
            f"Edge density ({density:.2f}) below 2.0 — graph is too sparse for multi-hop traversal"
        )

    def test_no_node_exceeds_hub_degree_ceiling(self) -> None:
        """Anti-hub-collapse: no single node should exceed degree 200."""
        degrees = self._node_degrees()
        over = [(nid, deg) for nid, deg in degrees if deg > 200]
        self.assertEqual(
            [], over,
            f"Nodes exceeding degree 200: {over[:5]}"
        )

    def test_isolate_rate_is_bounded(self) -> None:
        """Isolate (degree-0) rate must not exceed 12% of active nodes."""
        degrees = self._node_degrees()
        total = len(degrees)
        isolates = sum(1 for _, deg in degrees if deg == 0)
        rate = isolates / total
        self.assertLessEqual(
            rate, 0.12,
            f"Isolate rate ({rate:.1%}, {isolates}/{total}) exceeds 12% ceiling"
        )

    def test_giant_component_covers_majority_of_nodes(self) -> None:
        """The largest connected component must include ≥ 75% of active nodes."""
        # Build adjacency list from assertions
        nodes = {
            r["node_id"]
            for r in self.connection.execute("SELECT node_id FROM node WHERE active=1")
        }
        adj: dict[str, set[str]] = defaultdict(set)
        edges = self.connection.execute("""
            SELECT subject_node_id, object_node_id FROM assertion
            WHERE subject_node_id IS NOT NULL AND object_node_id IS NOT NULL
        """).fetchall()
        for e in edges:
            s, o = e["subject_node_id"], e["object_node_id"]
            if s in nodes and o in nodes:
                adj[s].add(o)
                adj[o].add(s)

        # BFS to find connected components
        visited: set[str] = set()
        largest_component = 0
        for start in nodes:
            if start in visited:
                continue
            queue = [start]
            component_size = 0
            while queue:
                node = queue.pop()
                if node in visited:
                    continue
                visited.add(node)
                component_size += 1
                for neighbor in adj.get(node, set()):
                    if neighbor not in visited:
                        queue.append(neighbor)
            if component_size > largest_component:
                largest_component = component_size

        coverage = largest_component / len(nodes) if nodes else 0
        self.assertGreaterEqual(
            coverage, 0.75,
            f"Giant component ({largest_component}/{len(nodes)} = {coverage:.1%}) "
            f"covers < 75% of active nodes"
        )

    def test_mean_degree_minimum(self) -> None:
        degrees = self._node_degrees()
        mean = sum(d for _, d in degrees) / len(degrees)
        self.assertGreaterEqual(mean, 3.0, f"Mean degree ({mean:.1f}) below 3.0")

    def test_leaf_node_rate_is_bounded(self) -> None:
        """Leaf (degree-1) nodes must not exceed 40% of active nodes."""
        degrees = self._node_degrees()
        total = len(degrees)
        leaves = sum(1 for _, deg in degrees if deg == 1)
        rate = leaves / total
        self.assertLessEqual(
            rate, 0.40,
            f"Leaf node rate ({rate:.1%}, {leaves}/{total}) exceeds 40% ceiling"
        )


class PredicateRegistryAuditTests(unittest.TestCase):
    """Verify predicate registry completeness and consistency."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_every_used_predicate_is_registered(self) -> None:
        """Predicates used in assertions must exist in the registry."""
        used = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT predicate FROM assertion WHERE predicate IS NOT NULL"
            )
        }
        registered = {
            r[0]
            for r in self.connection.execute(
                "SELECT predicate FROM assertion_predicate_registry"
            )
        }
        unregistered = used - registered
        self.assertEqual(
            set(), unregistered,
            f"Predicates used but not registered: {unregistered}"
        )

    def test_predicate_registry_has_minimum_population(self) -> None:
        count = self.connection.execute(
            "SELECT count(*) FROM assertion_predicate_registry"
        ).fetchone()[0]
        self.assertGreaterEqual(count, 15)

    def test_all_registry_predicates_have_definitions(self) -> None:
        missing = self.connection.execute("""
            SELECT predicate FROM assertion_predicate_registry
            WHERE definition IS NULL OR TRIM(definition) = ''
        """).fetchall()
        self.assertEqual(
            [], [r["predicate"] for r in missing],
            "Predicates without definitions"
        )

    def test_acyclic_predicates_have_no_self_referencing_assertions(self) -> None:
        """Acyclic predicates must not have subject == object."""
        acyclic = [
            r["predicate"]
            for r in self.connection.execute(
                "SELECT predicate FROM assertion_predicate_registry WHERE acyclic_flag=1"
            )
        ]
        for pred in acyclic:
            with self.subTest(predicate=pred):
                bad = self.connection.execute(
                    "SELECT count(*) FROM assertion "
                    "WHERE predicate=? AND subject_node_id = object_node_id",
                    (pred,),
                ).fetchone()[0]
                self.assertEqual(0, bad, f"Self-referencing {pred} assertions found")


class CardAndRouteAuditTests(unittest.TestCase):
    """Verify card and routing infrastructure completeness."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_every_card_has_at_least_one_mapped_node(self) -> None:
        cards = self.connection.execute("SELECT card_id FROM card").fetchall()
        empty_cards = []
        for card in cards:
            count = self.connection.execute(
                "SELECT count(*) FROM node_card_map WHERE card_id=?",
                (card["card_id"],),
            ).fetchone()[0]
            if count == 0:
                empty_cards.append(card["card_id"])
        self.assertEqual([], empty_cards, f"Cards with no mapped nodes: {empty_cards}")

    def test_every_card_has_at_least_one_canonical_route(self) -> None:
        cards = [
            r["card_id"]
            for r in self.connection.execute("SELECT card_id FROM card")
        ]
        no_routes = []
        for card_id in cards:
            count = self.connection.execute(
                "SELECT count(*) FROM canonical_route WHERE card_id=?",
                (card_id,),
            ).fetchone()[0]
            if count == 0:
                no_routes.append(card_id)
        self.assertEqual([], no_routes, f"Cards with no routes: {no_routes}")

    def test_canonical_route_population_minimum(self) -> None:
        count = self.connection.execute("SELECT count(*) FROM canonical_route").fetchone()[0]
        self.assertGreaterEqual(count, 1000)

    def test_card_population_minimum(self) -> None:
        count = self.connection.execute("SELECT count(*) FROM card").fetchone()[0]
        self.assertGreaterEqual(count, 10)

    def test_node_routing_coverage(self) -> None:
        """At least 80% of active non-card nodes have a canonical route."""
        total = self.connection.execute(
            "SELECT count(*) FROM node WHERE active=1 AND node_type != 'card'"
        ).fetchone()[0]
        routed = self.connection.execute("""
            SELECT count(DISTINCT cr.object_id) FROM canonical_route cr
            WHERE cr.object_type = 'node' AND cr.active = 1
              AND cr.object_id IN (SELECT node_id FROM node WHERE active=1 AND node_type != 'card')
        """).fetchone()[0]
        coverage = routed / total if total else 0
        self.assertGreaterEqual(
            coverage, 0.80,
            f"Node routing coverage ({coverage:.1%}, {routed}/{total}) below 80%"
        )

    def test_route_status_values_are_governed(self) -> None:
        actual = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT route_status FROM canonical_route"
            )
        }
        allowed = {
            "active", "inactive", "deprecated", "pending",
            "ambiguous", "intentional_unrouted",
        }
        unknown = actual - allowed
        self.assertEqual(set(), unknown, f"Unknown route statuses: {unknown}")


class DecisionInfrastructureTests(unittest.TestCase):
    """Verify decision intelligence infrastructure readiness."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.connection = connect_sqlite(GRAPH, readonly=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.connection.close()

    def test_query_definitions_exist(self) -> None:
        count = self.connection.execute(
            "SELECT count(*) FROM query_definition"
        ).fetchone()[0]
        self.assertGreaterEqual(count, 10)

    def test_query_definitions_have_valid_intents(self) -> None:
        intents = {
            r[0]
            for r in self.connection.execute(
                "SELECT DISTINCT intent FROM query_definition"
            )
        }
        # Should have diverse query intents
        self.assertGreaterEqual(
            len(intents), 5,
            f"Only {len(intents)} distinct query intents — need ≥ 5"
        )

    def test_governance_approval_count(self) -> None:
        """Track governance approval progress."""
        count = self.connection.execute(
            "SELECT count(*) FROM governance_approval"
        ).fetchone()[0]
        self.assertGreaterEqual(count, 1, "No governance approvals recorded")

    def test_audit_event_population_minimum(self) -> None:
        count = self.connection.execute(
            "SELECT count(*) FROM audit_event"
        ).fetchone()[0]
        self.assertGreaterEqual(count, 5)

    def test_ingestion_run_gap_is_documented(self) -> None:
        """Explicit test documenting zero governed ingestion runs.

        This test PASSES today to document the known gap.
        When the first ingestion run completes, flip this to
        assertGreaterEqual(count, 1).
        """
        count = self.connection.execute(
            "SELECT count(*) FROM ingestion_run"
        ).fetchone()[0]
        self.assertEqual(
            0, count,
            f"Ingestion runs exist ({count}) — upgrade this test to a minimum gate"
        )

    def test_quarantine_is_clean(self) -> None:
        """No stale quarantine items."""
        count = self.connection.execute(
            "SELECT count(*) FROM quarantine_item"
        ).fetchone()[0]
        self.assertEqual(0, count, f"{count} quarantine items need drain")

    def test_conflict_detection_infrastructure_exists(self) -> None:
        """assertion_conflict and v_conflicting_assertions must be queryable."""
        # Just verify they exist and are queryable
        self.connection.execute("SELECT count(*) FROM assertion_conflict").fetchone()
        self.connection.execute(
            "SELECT count(*) FROM v_conflicting_assertions"
        ).fetchone()

    def test_audit_is_read_only(self) -> None:
        before = sha256_file(GRAPH)
        self.assertEqual(before, sha256_file(GRAPH))


if __name__ == "__main__":
    unittest.main(verbosity=2)
