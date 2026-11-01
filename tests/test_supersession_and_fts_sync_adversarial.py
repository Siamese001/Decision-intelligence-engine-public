#!/usr/bin/env python3
"""Adversarial and boundary tests for supersession invariants and FTS5 synchronization."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.agents.supersession import (
    SupersessionResolver,
    _is_functional_predicate,
)
from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy
from dual_pipeline.graph_scout_core.scans import scan_temporal_contradictions


class TestSupersessionAndFtsSyncAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="supersession_adv_")
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
                """CREATE TABLE source (
                    source_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    date TEXT,
                    authority_rank INTEGER DEFAULT 1
                )"""
            )
            conn.execute(
                """CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object_node_id TEXT,
                    object_literal TEXT,
                    canonical_status TEXT NOT NULL,
                    validation_state TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    classification TEXT NOT NULL,
                    allowed_audiences_json TEXT NOT NULL,
                    qualifiers_json TEXT NOT NULL,
                    primary_source_id TEXT,
                    superseded_by_assertion_id TEXT,
                    superseded_at TEXT,
                    valid_from TEXT,
                    recorded_at TEXT,
                    confidence_score REAL,
                    scope_text TEXT,
                    updated_at TEXT
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
                """CREATE TABLE evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    direct_quote TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE search_document (
                    search_document_id TEXT PRIMARY KEY,
                    object_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    canonical_status TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    source_id TEXT NOT NULL,
                    assertion_id TEXT NOT NULL,
                    updated_at TEXT
                )"""
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_adversarial_is_functional_predicate_contract(self) -> None:
        """Functional predicate classification strictly separates single-valued from multi-valued."""
        self.assertFalse(_is_functional_predicate("HAS_CLAIM_TEXT"))
        self.assertFalse(_is_functional_predicate("has_claim_text"))
        self.assertFalse(_is_functional_predicate("EXTERNAL_CLAIM_TEXT"))
        self.assertFalse(_is_functional_predicate("MENTIONED"))
        self.assertFalse(_is_functional_predicate("DISCUSSED"))

        # Genuinely functional predicates must return True
        self.assertTrue(_is_functional_predicate("REPORTS_TO"))
        self.assertTrue(_is_functional_predicate("HAS_ROLE"))
        self.assertTrue(_is_functional_predicate("HAS_TITLE"))
        self.assertTrue(_is_functional_predicate("HAS_STATUS"))
        self.assertTrue(_is_functional_predicate("DEPLOYMENT_STATUS"))
        self.assertTrue(_is_functional_predicate("STATUS_PLANNED"))

    def test_adversarial_functional_predicate_still_supersedes_correctly(self) -> None:
        """Genuine functional predicate contradictions (e.g. REPORTS_TO change) are still detected."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("INSERT INTO node VALUES ('person_lori', 'Lori Bartlett', 'Lori', 'person', 1)")
            conn.execute("INSERT INTO node VALUES ('person_matt', 'Matt Barrows', 'Matt', 'person', 1)")
            conn.execute("INSERT INTO node VALUES ('person_ed', 'Ed Higgins', 'Ed', 'person', 1)")
            conn.execute("INSERT INTO source VALUES ('src_1', 'Org Chart July', '2026-07-01', 1)")
            conn.execute("INSERT INTO source VALUES ('src_2', 'Org Chart October', '2026-10-01', 2)")
            conn.execute("INSERT INTO evidence_item VALUES ('ev_1', 'src_1', 'Lori reports to Matt')")
            conn.execute("INSERT INTO evidence_item VALUES ('ev_2', 'src_2', 'Lori reports to Ed')")

            conn.execute(
                """INSERT INTO assertion VALUES
                   ('asrt_rep_1', 'person_lori', 'REPORTS_TO', 'person_matt', NULL,
                    'canonical', 'approved', 'current', 1, 'internal', '["ENGINEERING"]', '{}',
                    'src_1', NULL, NULL, '2026-07-01', '2026-07-01T00:00:00Z', 0.9, 'reports to', '2026-07-01')"""
            )
            conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_rep_1', 'ev_1')")

            conn.execute(
                """INSERT INTO assertion VALUES
                   ('asrt_rep_2', 'person_lori', 'REPORTS_TO', 'person_ed', NULL,
                    'canonical', 'approved', 'current', 1, 'internal', '["ENGINEERING"]', '{}',
                    'src_2', NULL, NULL, '2026-10-01', '2026-10-01T00:00:00Z', 0.9, 'reports to', '2026-10-01')"""
            )
            conn.execute("INSERT INTO assertion_evidence VALUES ('asrt_rep_2', 'ev_2')")
            conn.commit()

            # Functional contradiction should be found
            findings = scan_temporal_contradictions(conn)
            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0].predicate, "REPORTS_TO")

            # SupersessionResolver should supersede older org chart
            resolver = SupersessionResolver()
            policy = GraphFreshnessPolicy(supersession_strategy="highest_authority_only")
            res = resolver.scan(conn, policy)
            self.assertEqual(res.superseded_mapping.get("asrt_rep_1"), "asrt_rep_2")
        finally:
            conn.close()

    def test_adversarial_multi_speaker_statements_never_superseded(self) -> None:
        """High-volume statements by 5 speakers across 10 meetings are completely immune to supersession."""
        conn = sqlite3.connect(self.db_path)
        try:
            for s_idx in range(5):
                speaker_id = f"speaker_{s_idx}"
                conn.execute(
                    "INSERT INTO node VALUES (?, ?, ?, ?, ?)",
                    (speaker_id, f"Speaker {s_idx}", "Speaker", "person", 1),
                )
                for m_idx in range(10):
                    src_id = f"src_{s_idx}_{m_idx}"
                    asrt_id = f"asrt_{s_idx}_{m_idx}"
                    ev_id = f"ev_{s_idx}_{m_idx}"
                    dt_str = f"2026-10-{m_idx+1:02d}"
                    conn.execute(
                        "INSERT INTO source VALUES (?, ?, ?, ?)",
                        (src_id, f"Meeting {m_idx}", dt_str, 1),
                    )
                    conn.execute(
                        "INSERT INTO evidence_item VALUES (?, ?, ?)",
                        (ev_id, src_id, f"Statement {m_idx}"),
                    )
                    conn.execute(
                        """INSERT INTO assertion VALUES
                           (?, ?, 'HAS_CLAIM_TEXT', NULL, ?,
                            'canonical', 'source_statement', 'current', 1, 'internal', '["ENGINEERING"]', '{}',
                            ?, NULL, NULL, ?, ?,
                            0.9, 'turn', ?)""",
                        (
                            asrt_id,
                            speaker_id,
                            f"Discussion turn {m_idx}",
                            src_id,
                            dt_str,
                            f"{dt_str}T00:00:00Z",
                            dt_str,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO assertion_evidence VALUES (?, ?)",
                        (asrt_id, ev_id),
                    )
            conn.commit()

            # Zero temporal contradictions across all 50 statements
            findings = scan_temporal_contradictions(conn)
            self.assertEqual(len(findings), 0)

            resolver = SupersessionResolver()
            policy = GraphFreshnessPolicy(supersession_strategy="newest_source_only")
            res = resolver.scan(conn, policy)
            self.assertEqual(len(res.superseded_mapping), 0)
        finally:
            conn.close()

    def test_adversarial_empty_assertions_table_does_not_crash(self) -> None:
        """Empty tables return empty findings without uncaught exceptions."""
        conn = sqlite3.connect(self.db_path)
        try:
            findings = scan_temporal_contradictions(conn)
            self.assertEqual(findings, [])

            resolver = SupersessionResolver()
            policy = GraphFreshnessPolicy(supersession_strategy="newest_source_only")
            res = resolver.scan(conn, policy)
            self.assertEqual(len(res.superseded_mapping), 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
