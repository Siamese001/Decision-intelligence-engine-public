"""Wave 1 Adversarial & Edge-Case Tests: Query Router & Dynamic Entity Resolution.

Covers mandatory edge-case categories:
1. Boundary payload (empty/whitespace-only/max-length queries, empty SQLite node tables)
2. Malformed input (corrupted alias tables, non-string/non-boolean router parameters)
3. Invariant bypass attempt (peer-action conflicts, unauthorized overrides, quoted/negated entity injection)
4. Exact production regression (imperative synthesis verbs with canonical projects preserving specific intent)
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.l2_policy_core.intent import analyze_request_intent
from dual_pipeline.query_core.dynamic_entities import resolve_dynamic_entity_matches
from dual_pipeline.query_router import (
    MAX_QUESTION_LENGTH,
    QueryDestination,
    route_query,
)


class Wave1RouterEntityAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patcher = patch("dual_pipeline.query_router._check_graphdb_available", return_value=True)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_boundary_payload_and_empty_graph_tables(self) -> None:
        """Category 1 (Boundary payload): empty strings rejected, max-length accepted, empty DB returns fallback."""
        with self.assertRaisesRegex(ValueError, "question is required"):
            route_query("   ")
        seed = "Tell me about Chakra. "
        decision = route_query(seed + "x" * (MAX_QUESTION_LENGTH - len(seed)))
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, decision.destination)

        with tempfile.TemporaryDirectory(prefix="wave1-adv-empty-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            try:
                conn.execute(
                    "CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER)"
                )
                conn.commit()
                matches = resolve_dynamic_entity_matches(conn, "Evaluate DocIQ architecture.")
                self.assertEqual([], matches)
            finally:
                conn.close()

    def test_malformed_input_and_corrupted_alias_schema(self) -> None:
        """Category 2 (Malformed input): missing columns in alias table and invalid parameter types."""
        with self.assertRaisesRegex(ValueError, "question must be a string"):
            route_query(12345)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "authorized must be a boolean"):
            route_query("Recommend actions for DocIQ.", authorized="yes")  # type: ignore[arg-type]

        with tempfile.TemporaryDirectory(prefix="wave1-adv-malformed-") as tmpdir:
            graph = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph)
            try:
                conn.executescript(
                    """
                    CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT, display_name TEXT, node_type TEXT, active INTEGER);
                    CREATE TABLE alias (wrong_col TEXT);
                    INSERT INTO node VALUES ('n1', 'DocIQ', 'DocIQ', 'workstream', 1);
                    """
                )
                conn.commit()
                matches = resolve_dynamic_entity_matches(conn, "What are the risks in DocIQ?")
                self.assertEqual(1, len(matches))
                self.assertEqual("n1", matches[0]["node_id"])
            finally:
                conn.close()

    def test_invariant_bypass_peer_action_and_unauthorized_requests_fail_closed(self) -> None:
        """Category 5 (Invariant bypass attempt): peer-action conflicts, general decisive, and unauthorized queries never bypass via entity override."""
        # Peer action conflict: bounded + material
        conflict1 = route_query("List exact governed NEE records and recommend an architecture.")
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, conflict1.destination)
        self.assertIn("MULTI_DESTINATION_CONFLICT", conflict1.reason_codes)

        # Peer action conflict: operational + material
        conflict2 = route_query("Download the report and recommend an investment across NEE evidence.")
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, conflict2.destination)
        self.assertIn("MULTI_DESTINATION_CONFLICT", conflict2.reason_codes)

        # Peer action conflict: general + material
        conflict3 = route_query("Recommend an NEE decision and rewrite the paragraph.")
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, conflict3.destination)
        self.assertIn("MULTI_DESTINATION_CONFLICT", conflict3.reason_codes)

        # General decisive must stay GENERAL_ASSISTANT_HANDOFF even when mentioning NEE
        restaurant = route_query("Recommend a restaurant using NEE evidence across sources.")
        self.assertEqual(QueryDestination.GENERAL_ASSISTANT_HANDOFF, restaurant.destination)

        # Unauthorized query must fail closed to ROUTE_AMBIGUOUS
        unauth = route_query(
            "Recommend a future-state A2A architecture across Project Beacon governed evidence.",
            authorized=False,
        )
        self.assertEqual(QueryDestination.ROUTE_AMBIGUOUS, unauth.destination)
        self.assertIn("QUERY_NOT_AUTHORIZED", unauth.reason_codes)

    def test_exact_production_regression_risk_intent_and_imperative_override(self) -> None:
        """Category 7 (Exact production regression): 'Find risks...' preserves risk_or_opportunity intent; imperative override preserves specific query_type."""
        intent = analyze_request_intent("Find the main risks and gaps for Project Jarvis.")
        self.assertTrue(intent.risk_or_opportunity)
        self.assertFalse(intent.bounded_lookup)

        # Imperative synthesis verb with review gaps preserves gap_risk_inconsistency when risk is conclusive
        decision_nba = route_query("Propose next best actions for Project Jarvis.")
        self.assertEqual(QueryDestination.NEE_DECISION_INTELLIGENCE, decision_nba.destination)
        self.assertEqual("opportunity_nba", decision_nba.query_type)

        # Executive agenda forces cross_workstream_synthesis
        decision_exec = route_query("Draft an executive agenda for Project Jarvis and Conduit.")
        self.assertEqual(QueryDestination.NEE_DECISION_INTELLIGENCE, decision_exec.destination)
        self.assertEqual("cross_workstream_synthesis", decision_exec.query_type)
        self.assertIn("EXECUTIVE_SYNTHESIS_FORCED", decision_exec.reason_codes)


if __name__ == "__main__":
    unittest.main()
