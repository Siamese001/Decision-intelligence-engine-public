"""Wave 2 tests for breaking speaker star-graph isolation via co-source and secondary entity bridging."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.graph_cache import read_graph_from_disk


def _build_speaker_star_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT,
                active INTEGER DEFAULT 1
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                materiality TEXT,
                canonical_status TEXT,
                validation_state TEXT,
                currentness_state TEXT,
                retrieval_eligible INTEGER,
                primary_source_id TEXT,
                recorded_at TEXT,
                created_at TEXT,
                confidence_score REAL
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                evidence_relationship TEXT
            );
            CREATE VIEW v_canonical_edge AS
            SELECT
                assertion_id,
                subject_node_id AS from_node_id,
                predicate AS relationship_family,
                predicate AS edge_type,
                object_node_id AS to_node_id,
                materiality,
                primary_source_id AS source_id
            FROM assertion
            WHERE object_node_id IS NOT NULL AND currentness_state = 'current';
            CREATE VIEW v_conflicting_assertions AS
            SELECT NULL AS assertion_id_1, NULL AS assertion_id_2, 'resolved' AS resolution_state
            WHERE 0;
            """
        )
        conn.executemany(
            "INSERT INTO node (node_id, canonical_name, node_type, active) VALUES (?, ?, ?, 1)",
            [
                ("person_chakra", "Chakra Sankaraiah", "person"),
                ("person_mark_mccarthy", "Mark McCarthy", "person"),
                ("org_nea", "Apex Global Analytics", "org"),
                ("project:rewire", "Rewire Program", "project"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, legacy_predicate,
                object_node_id, object_literal, materiality, canonical_status,
                validation_state, currentness_state, retrieval_eligible,
                primary_source_id, recorded_at, created_at, confidence_score
            ) VALUES (?, ?, 'HAS_CLAIM_TEXT', NULL, NULL, ?, 'high', 'canonical', 'approved', 'current', 1, ?, '2026-08-27', '2026-08-27', 0.90)
            """,
            [
                (
                    "asrt-chakra-pod-ban",
                    "person_chakra",
                    "NEA engineering staffing model standardizes on 2 to 3 multifaceted full-stack engineers per workstream, phasing out segregated pods.",
                    "source-chakra-op-model",
                ),
                (
                    "asrt-mark-pineapple",
                    "person_mark_mccarthy",
                    "Technical lead formal designation at Apex Global Analytics occurs after 4 to 6 months of demonstrated performance.",
                    "source-chakra-op-model",
                ),
                (
                    "asrt-org-nea-anchor",
                    "org_nea",
                    "Apex Global Analytics operating model governance session.",
                    "source-chakra-op-model",
                ),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class SpeakerStarGraphBridgingTests(unittest.TestCase):
    def test_co_source_org_bridges_speaker_bound_claims(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            _build_speaker_star_graph(db_path)
            _nodes, _assertions, _sup, adjacency, _contra, _conf, _excl = read_graph_from_disk(
                db_path
            )
            nea_neighbors = {edge["to_node_id"] for edge in adjacency.get("org_nea", [])}
            self.assertIn("person_chakra", nea_neighbors)
            self.assertIn("person_mark_mccarthy", nea_neighbors)

            chakra_neighbors = {edge["to_node_id"] for edge in adjacency.get("person_chakra", [])}
            self.assertIn("org_nea", chakra_neighbors)


if __name__ == "__main__":
    unittest.main()
