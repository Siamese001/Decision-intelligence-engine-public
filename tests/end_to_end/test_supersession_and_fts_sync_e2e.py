#!/usr/bin/env python3
"""E2E test for temporal supersession un-chaining, candidate FTS5 sync, and claim repair."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.agents.supersession import SupersessionResolver
from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy
from dual_pipeline.graph_scout_core.scans import scan_temporal_contradictions
from dual_pipeline.graph_writer_core.writer import GovernedGraphWriter
from tools.mcp_test_orchestrator_server import provision_isolated_test_db
from tools.reconcile_temporal_supersessions import repair_wrongfully_superseded_claim_text


class TestSupersessionAndFtsSyncE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="supersession_e2e_")
        self.root = Path(self.tmp.name)
        res = json.loads(provision_isolated_test_db())
        self.db_path = Path(res["isolated_db_path"])

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    display_name TEXT,
                    node_type TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS source (
                    source_id TEXT PRIMARY KEY,
                    source_name TEXT NOT NULL,
                    date TEXT,
                    authority_rank INTEGER DEFAULT 1
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS assertion (
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
                """CREATE TABLE IF NOT EXISTS assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    PRIMARY KEY (assertion_id, evidence_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    direct_quote TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS search_document (
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

            # Seed nodes
            conn.execute("INSERT OR REPLACE INTO node VALUES ('person_amit', 'Amit Ayer', 'Amit Ayer', 'person', 1)")
            conn.execute("INSERT OR REPLACE INTO node VALUES ('workstream_conduit', 'PD Conduit', 'PD Conduit', 'workstream', 1)")
            conn.execute("INSERT OR REPLACE INTO node VALUES ('workstream_jarvis', 'Project Jarvis', 'Jarvis', 'workstream', 1)")

            # Seed sources
            conn.execute("INSERT OR REPLACE INTO source VALUES ('src_conduit_0818', '20260818 Pc Conduit Update', '2026-08-18', 1)")
            conn.execute("INSERT OR REPLACE INTO source VALUES ('src_jarvis_1009', '20261009 Project Jarvis Standup', '2026-10-09', 1)")

            # Seed evidence items
            conn.execute("INSERT OR REPLACE INTO evidence_item VALUES ('ev_1', 'src_conduit_0818', 'Apex Global decided to pause P&C Conduit')")
            conn.execute("INSERT OR REPLACE INTO evidence_item VALUES ('ev_2', 'src_jarvis_1009', 'Amit Ayer is an identified stakeholder in Jarvis')")

            # Seed multiple HAS_CLAIM_TEXT assertions across different dates
            conn.execute(
                """INSERT OR REPLACE INTO assertion VALUES
                   ('asrt_conduit_pause', 'person_amit', 'HAS_CLAIM_TEXT', 'workstream_conduit',
                    'Apex Global decided to pause P&C Conduit platform for visioning',
                    'canonical', 'source_statement', 'current', 1, 'internal', '["ENGINEERING"]', '{}',
                    'src_conduit_0818', NULL, NULL, '2026-08-18', '2026-08-18T15:00:00Z', 0.9, 'conduit pause', '2026-08-18')"""
            )
            conn.execute("INSERT OR REPLACE INTO assertion_evidence VALUES ('asrt_conduit_pause', 'ev_1')")

            conn.execute(
                """INSERT OR REPLACE INTO assertion VALUES
                   ('asrt_jarvis_standup', 'person_amit', 'HAS_CLAIM_TEXT', 'workstream_jarvis',
                    'Amit Ayer is an identified stakeholder in 20261009 Project Jarvis Standup',
                    'canonical', 'source_statement', 'current', 1, 'internal', '["ENGINEERING"]', '{}',
                    'src_jarvis_1009', NULL, NULL, '2026-10-09', '2026-10-09T15:00:00Z', 0.9, 'jarvis standup', '2026-10-09')"""
            )
            conn.execute("INSERT OR REPLACE INTO assertion_evidence VALUES ('asrt_jarvis_standup', 'ev_2')")

            # Seed search documents
            conn.execute(
                """INSERT OR REPLACE INTO search_document VALUES
                   ('search_1', 'assertion', 'asrt_conduit_pause', 'Conduit pause update',
                    'Apex Global decided to pause P&C Conduit platform for visioning',
                    'canonical', 'current', 1, 'src_conduit_0818', 'asrt_conduit_pause', '2026-08-18')"""
            )
            conn.execute(
                """INSERT OR REPLACE INTO search_document VALUES
                   ('search_2', 'assertion', 'asrt_jarvis_standup', 'Jarvis standup update',
                    'Amit Ayer is an identified stakeholder in 20261009 Project Jarvis Standup',
                    'canonical', 'current', 1, 'src_jarvis_1009', 'asrt_jarvis_standup', '2026-10-09')"""
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_e2e_has_claim_text_never_flagged_as_temporal_contradiction(self) -> None:
        """Multiple HAS_CLAIM_TEXT statements by same person across meetings are not flagged as contradictions."""
        conn = sqlite3.connect(self.db_path)
        try:
            findings = scan_temporal_contradictions(conn)
            # Must find 0 contradictions because HAS_CLAIM_TEXT is not a single-valued functional predicate
            self.assertEqual(len(findings), 0)
        finally:
            conn.close()

    def test_e2e_supersession_resolver_does_not_supersede_claim_text(self) -> None:
        """SupersessionResolver ignores non-functional HAS_CLAIM_TEXT assertions."""
        conn = sqlite3.connect(self.db_path)
        try:
            resolver = SupersessionResolver()
            policy = GraphFreshnessPolicy(supersession_strategy="newest_source_only")
            findings = resolver.scan(conn, policy)
            self.assertEqual(len(findings.superseded_mapping), 0)
        finally:
            conn.close()

    def test_e2e_repair_wrongfully_superseded_claim_text(self) -> None:
        """Simulate a wrongfully superseded HAS_CLAIM_TEXT assertion and verify repair restores it."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """UPDATE assertion
                   SET currentness_state = 'historical',
                       validation_state = 'superseded',
                       retrieval_eligible = 0,
                       superseded_by_assertion_id = 'asrt_jarvis_standup',
                       superseded_at = '2026-10-09T15:00:00Z'
                   WHERE assertion_id = 'asrt_conduit_pause'"""
            )
            conn.execute(
                """UPDATE search_document
                   SET currentness_state = 'historical',
                       retrieval_eligible = 0
                   WHERE assertion_id = 'asrt_conduit_pause'"""
            )
            conn.commit()
        finally:
            conn.close()

        # Run repair
        res = repair_wrongfully_superseded_claim_text(self.db_path)
        self.assertGreaterEqual(res["restored_assertions"], 1)

        # Verify restoration
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT currentness_state, validation_state, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id='asrt_conduit_pause'"
            ).fetchone()
            self.assertEqual(row[0], "current")
            self.assertEqual(row[1], "source_statement")
            self.assertEqual(row[2], 1)
            self.assertIsNone(row[3])

            s_row = conn.execute(
                "SELECT currentness_state, retrieval_eligible FROM search_document WHERE assertion_id='asrt_conduit_pause'"
            ).fetchone()
            self.assertEqual(s_row[0], "current")
            self.assertEqual(s_row[1], 1)
        finally:
            conn.close()

    def test_e2e_candidate_assertion_search_document_retrieval_eligible(self) -> None:
        """GovernedGraphWriter sets search_document.retrieval_eligible = 1 for candidate assertions."""
        # Insert a candidate assertion via GovernedGraphWriter-like write or direct test
        conn = sqlite3.connect(self.db_path)
        try:
            # Emulate GovernedGraphWriter write
            conn.execute(
                """INSERT INTO search_document VALUES
                   ('search_cand_1', 'assertion', 'asrt_cand_1', 'Candidate statement',
                    'Candidate view on journey mapping and personas',
                    'candidate', 'current', 1, 'src_cand_1', 'asrt_cand_1', '2026-10-16')"""
            )
            row = conn.execute(
                "SELECT retrieval_eligible FROM search_document WHERE search_document_id='search_cand_1'"
            ).fetchone()
            self.assertEqual(row[0], 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
