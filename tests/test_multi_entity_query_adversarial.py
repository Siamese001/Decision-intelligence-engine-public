#!/usr/bin/env python3
"""Adversarial and boundary tests for multi-entity query decomposition and entity boundaries."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.query_core.dynamic_entities import resolve_dynamic_entity_matches
from dual_pipeline.query_core.hydration import exact_retrieval
from dual_pipeline.query_core.planning import _split_multi_subject_clauses
from dual_pipeline.query_core.reasoning import reason_over_packet, render_markdown


class TestMultiEntityQueryAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="entity_boundary_adv_")
        self.db_path = Path(self.tmp.name) / "test_graph.sqlite"
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    display_name TEXT,
                    node_type TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )"""
            )
            conn.execute(
                """CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_node_id TEXT,
                    canonical_status TEXT NOT NULL,
                    validation_state TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    classification TEXT NOT NULL,
                    allowed_audiences_json TEXT NOT NULL,
                    qualifiers_json TEXT NOT NULL
                )"""
            )
            conn.execute(
                """CREATE TABLE assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    PRIMARY KEY (assertion_id, evidence_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE search_document (
                    search_document_id INTEGER PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    canonical_status TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    assertion_id TEXT NOT NULL
                )"""
            )

            # Seed nodes with substring similarities
            nodes = [
                ("node_lori", "Lori Bartlett", "Lori Bartlett", "person", 1),
                ("node_noemi", "Noemi Monnerville", "Noemi Monnerville", "person", 1),
                ("node_florida", "Apex Power & Grid", "Florida Power", "organization", 1),
                ("node_review", "Sprint Review", "Sprint Review", "meeting", 1),
                ("node_conduit", "PD Conduit / Conductor", "PD Conduit", "workstream", 1),
                # Two entities with the same given name to test ambiguity handling
                ("node_john_smith", "John Smith", "John Smith", "person", 1),
                ("node_john_doe", "John Doe", "John Doe", "person", 1),
            ]
            conn.executemany("INSERT INTO node VALUES (?, ?, ?, ?, ?)", nodes)

            # Seed assertions and search docs
            conn.execute(
                """INSERT INTO assertion VALUES
                   ('asrt-1', 'node_lori', 'MENTIONED', 'node_conduit', 'canonical', 'approved',
                    'current', 1, 'INTERNAL', '["ENGINEERING"]', '{}')"""
            )
            conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-1', 'ev-1')")
            conn.execute(
                """INSERT INTO search_document VALUES
                   (1, 'assertion', 'asrt-1', 'Lori debrief', 'Lori discussed Conduit status',
                    'canonical', 'current', 1, 'src-1', 'asrt-1')"""
            )

            conn.execute(
                """INSERT INTO assertion VALUES
                   ('asrt-2', 'node_florida', 'LOCATED_IN', NULL, 'canonical', 'approved',
                    'current', 1, 'INTERNAL', '["ENGINEERING"]', '{}')"""
            )
            conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-2', 'ev-2')")
            conn.execute(
                """INSERT INTO search_document VALUES
                   (2, 'assertion', 'asrt-2', 'Florida infrastructure', 'Florida operational grid overview',
                    'canonical', 'current', 1, 'src-2', 'asrt-2')"""
            )

            conn.execute(
                """INSERT INTO assertion VALUES
                   ('asrt-3', 'node_review', 'DISCUSSED', NULL, 'canonical', 'approved',
                    'current', 1, 'INTERNAL', '["ENGINEERING"]', '{}')"""
            )
            conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-3', 'ev-3')")
            conn.execute(
                """INSERT INTO search_document VALUES
                   (3, 'assertion', 'asrt-3', 'Sprint demo review', 'Review of engineering progress',
                    'canonical', 'current', 1, 'src-3', 'asrt-3')"""
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_adversarial_substring_collision_guards(self) -> None:
        """'Lori' must not match 'Florida', 'view' must not match 'Review'."""
        conn = sqlite3.connect(self.db_path)
        try:
            matches = resolve_dynamic_entity_matches(conn, "Lori debrief and view", authorized_nodes=None)
            node_ids = {m["node_id"] for m in matches}
            self.assertIn("node_lori", node_ids)
            self.assertNotIn("node_florida", node_ids)
            self.assertNotIn("node_review", node_ids)
        finally:
            conn.close()

    def test_adversarial_ambiguous_first_name_does_not_falsely_bind(self) -> None:
        """Ambiguous first name shared across multiple people must NOT falsely bind to one."""
        conn = sqlite3.connect(self.db_path)
        try:
            matches = resolve_dynamic_entity_matches(conn, "John debrief on priorities", authorized_nodes=None)
            node_ids = {m["node_id"] for m in matches}
            # Neither John Smith nor John Doe should be bound because "John" is ambiguous
            self.assertNotIn("node_john_smith", node_ids)
            self.assertNotIn("node_john_doe", node_ids)
        finally:
            conn.close()

    def test_adversarial_clause_splitting_complex_conjunctions(self) -> None:
        """Clauses split properly on 'and', ';', 'vs', 'versus', 'while'."""
        q1 = "Lori debrief on Conduit pause and Noemi view on KM"
        self.assertEqual(len(_split_multi_subject_clauses(q1)), 2)

        q2 = "Lori debrief; Noemi view; Ed perspective"
        self.assertEqual(len(_split_multi_subject_clauses(q2)), 3)

        q3 = "Conduit architecture vs Firecaster deployment"
        self.assertEqual(len(_split_multi_subject_clauses(q3)), 2)

        q4 = "Single clause question without conjunction"
        self.assertEqual(_split_multi_subject_clauses(q4), [])

    def test_adversarial_empty_and_whitespace_queries(self) -> None:
        """Empty, whitespace, and punctuation-only inputs do not crash dynamic matching."""
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(resolve_dynamic_entity_matches(conn, "", authorized_nodes=None), [])
            self.assertEqual(resolve_dynamic_entity_matches(conn, "   \n\t  ", authorized_nodes=None), [])
            self.assertEqual(resolve_dynamic_entity_matches(conn, "!@#$%^&*()_+", authorized_nodes=None), [])
        finally:
            conn.close()

    def test_adversarial_regex_injection_queries(self) -> None:
        """Regex metacharacters in query do not cause RegexErrors in exact_retrieval."""
        exact = exact_retrieval(
            "Lori (.*)+ [a-z] debrief on Conduit",
            self.db_path,
            classification="internal",
            audiences=["ENGINEERING"],
            retrieval_mode="factual",
        )
        self.assertIsInstance(exact, dict)
        matched_node_ids = {m["node_id"] for m in exact["entity_matches"]}
        self.assertIn("node_lori", matched_node_ids)

    def test_adversarial_unrelated_entity_has_no_cross_attribution(self) -> None:
        """If query asks for Lori and Noemi, Florida Power evidence is not attributed to Lori."""
        plan = {
            "query_type": "current_state_corroboration",
            "primary_entities": ["Lori Bartlett", "Noemi Monnerville"],
            "materiality": "medium",
        }
        packet = {
            "approved": [
                {
                    "record": {
                        "record_id": "rec-florida",
                        "record_type": "assertion",
                        "lane": "approved",
                        "title": "Florida Power Grid",
                        "retrieval_text": "Florida Power operations in Miami.",
                        "evidence_ids": ["ev-2"],
                    },
                    "retrieval_metadata": {"answer_use": "factual_answer"},
                }
            ],
            "candidate": [],
            "counterevidence": [],
            "gaps": [],
        }
        answer = reason_over_packet(plan, packet)
        # Because florida record does not mention Lori or Noemi tokens, it should NOT be in effective_factual
        self.assertEqual(len(answer["current_state"]), 0)
        self.assertTrue(any("No approved factual current-state evidence found" in g for g in packet["gaps"]))


if __name__ == "__main__":
    unittest.main()
