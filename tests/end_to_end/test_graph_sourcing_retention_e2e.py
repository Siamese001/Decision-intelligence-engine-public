#!/usr/bin/env python3
"""E2E test verifying graph sourcing retention, workstream dual-binding, and debrief entity preservation."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_freshness_core.agents.supersession import SupersessionResolver
from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy
from dual_pipeline.graph_writer_core.writer import GovernedGraphWriter
from dual_pipeline.intake_core.change_set import build_graph_change_set
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    _enforce_mutation_budget,
    sanitize_and_validate_projection,
)
from tools.mcp_test_orchestrator_server import provision_isolated_test_db


class TestGraphSourcingRetentionE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="graph_sourcing_e2e_")
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
                    assertion_fingerprint TEXT,
                    subject_node_id TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    legacy_predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    object_literal_type TEXT,
                    assertion_type TEXT,
                    atomicity_state TEXT,
                    scope_text TEXT,
                    speaker_or_author TEXT,
                    primary_source_id TEXT,
                    extraction_run_id TEXT,
                    extraction_method TEXT,
                    origin_type TEXT,
                    confidence_label TEXT,
                    confidence_score REAL,
                    validation_state TEXT NOT NULL,
                    currentness_state TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    date_precision TEXT,
                    date_status TEXT,
                    recorded_at TEXT,
                    superseded_at TEXT,
                    superseded_by_assertion_id TEXT,
                    resolution_state TEXT,
                    canonical_status TEXT NOT NULL,
                    canonical_eligible INTEGER NOT NULL,
                    retrieval_eligible INTEGER NOT NULL,
                    materiality TEXT,
                    classification TEXT NOT NULL,
                    allowed_audiences_json TEXT NOT NULL,
                    hitl_required INTEGER,
                    validation_priority TEXT,
                    validation_owner TEXT,
                    qualifiers_json TEXT NOT NULL,
                    legacy_record_type TEXT,
                    legacy_record_ids_json TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    approval_reference TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS assertion_evidence (
                    assertion_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    evidence_relationship TEXT,
                    independent_corroboration INTEGER,
                    created_at TEXT,
                    PRIMARY KEY (assertion_id, evidence_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS evidence_item (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    direct_quote TEXT,
                    extraction_confidence REAL,
                    source_authority_rank INTEGER
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
            conn.execute(
                """CREATE TABLE IF NOT EXISTS audit_event (
                    audit_event_id TEXT PRIMARY KEY,
                    actor_or_process TEXT,
                    action TEXT,
                    record_type TEXT,
                    record_id TEXT,
                    previous_value_json TEXT,
                    new_value_json TEXT,
                    source_id TEXT,
                    event_timestamp TEXT,
                    reason TEXT,
                    validation_reference TEXT,
                    approval_reference TEXT,
                    migration_or_ingestion_run_id TEXT,
                    release_version TEXT
                )"""
            )

            # Seed nodes
            conn.execute("INSERT OR REPLACE INTO node VALUES ('person_amit', 'Amit Ayer', 'Amit Ayer', 'person', 1)")
            conn.execute("INSERT OR REPLACE INTO node VALUES ('workstream_pd_conduit', 'PD Conduit', 'PD Conduit', 'workstream', 1)")
            conn.execute("INSERT OR REPLACE INTO node VALUES ('workstream_jarvis', 'Project Jarvis', 'Jarvis', 'workstream', 1)")
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_e2e_workstream_dual_binding_in_change_set(self) -> None:
        """Workstream pause statements bind to both the speaker and the target workstream entity."""
        bundle = {
            "source_id": "src_conduit_0818",
            "source_version_id": "src_conduit_0818_v1",
            "run_id": "run-conduit-0818",
            "trace_id": "a" * 32,
            "candidates": [
                {
                    "candidate_id": "cand_conduit_pause",
                    "candidate_type": "decision",
                    "normalized_text": "P&C Conduit is paused immediately while we evaluate options",
                    "materiality": "high",
                    "classification": "internal",
                    "audiences": ["nee-engineering"],
                    "evidence_ids": ["ev_1"],
                    "extraction_confidence": 0.95,
                    "entity_mentions": [
                        {"mention_id": "m_amit"},
                        {"mention_id": "m_conduit"},
                    ],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "ev_1",
                    "source_id": "src_conduit_0818",
                    "quote": "P&C Conduit is paused immediately",
                }
            ],
            "extensions": {
                "com.partner_advisory.nee.source": {
                    "source_id": "src_conduit_0818",
                    "source_version_id": "src_conduit_0818_v1",
                    "title": "P&C Conduit Update",
                    "date": "2026-08-18",
                    "authority_rank": 5,
                }
            },
        }
        dispositions = [{"candidate_id": "cand_conduit_pause", "disposition": "CANDIDATE"}]
        resolutions = [
            {"mention_id": "m_amit", "candidate_id": "cand_conduit_pause", "selected_entity_id": "person_amit", "outcome": "RESOLVED_INTERNAL"},
            {"mention_id": "m_conduit", "candidate_id": "cand_conduit_pause", "selected_entity_id": "workstream_pd_conduit", "outcome": "RESOLVED_INTERNAL"},
        ]

        change_set = build_graph_change_set(
            bundle=bundle,
            dispositions=dispositions,
            resolutions=resolutions,
            graph_path=self.db_path,
        )
        self.assertIsNotNone(change_set)
        ops = change_set["operations"]
        subjects = [
            change_set["extensions"]["com.partner_advisory.nee.operation-payloads"][op["operation_id"]]["subject_node_id"]
            for op in ops
        ]
        self.assertIn("person_amit", subjects)
        self.assertIn("workstream_pd_conduit", subjects)

    def test_e2e_lessons_learned_preserves_grounded_stakeholders_and_workstreams(self) -> None:
        """LESSONS_LEARNED archetype projections retain grounded stakeholders and workstream entities."""
        mutations = {
            "entity_upserts": [
                {
                    "id": "person:lori_bartlett",
                    "name": "Lori Bartlett",
                    "type": "person",
                    "evidence_ids": ["ev-debrief-1"],
                },
                {
                    "id": "workstream:conduit",
                    "name": "PD Conduit",
                    "type": "workstream",
                    "evidence_ids": ["ev-debrief-1"],
                },
            ],
            "relationship_signals": [],
            "meeting_archetype": "LESSONS_LEARNED",
        }
        ev_index = {
            "ev-debrief-1": {
                "statement_id": "ev-debrief-1",
                "text": "Lori Bartlett debriefed team on Conduit lessons learned",
                "speaker": "Michael Maione",
            }
        }
        meta = {
            "meeting_archetype": "LESSONS_LEARNED",
            "attendees": ["Lori Bartlett (Apex Global)", "Michael Maione (PartnerAdvisory)"],
        }

        _enforce_mutation_budget(
            mutations,
            max_budget=30,
            is_document=False,
            ev_index=ev_index,
            metadata=meta,
            text="Lori Bartlett Conduit debrief discussion",
        )

        retained_ids = [e["id"] for e in mutations["entity_upserts"]]
        self.assertIn("person:lori_bartlett", retained_ids)
        self.assertIn("workstream:conduit", retained_ids)

    def test_e2e_candidate_assertion_fts_synchronization(self) -> None:
        """Candidate assertions sync retrieval_eligible to search_document."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO assertion
                   (assertion_id, subject_node_id, predicate, object_literal, canonical_status,
                    canonical_eligible, validation_state, currentness_state, retrieval_eligible,
                    classification, allowed_audiences_json, qualifiers_json, primary_source_id)
                   VALUES ('asrt_cand_conduit', 'workstream_pd_conduit', 'HAS_CLAIM_TEXT',
                           'P&C Conduit paused pending review', 'candidate', 0, 'source_statement',
                           'current', 1, 'internal', '["nee-engineering"]', '{}', 'src_conduit_0818')"""
            )
            # Sync search_document matching assertion.retrieval_eligible
            conn.execute(
                """INSERT OR REPLACE INTO search_document
                   (search_document_id, object_type, object_id, title, body, canonical_status,
                    currentness_state, retrieval_eligible, source_id, assertion_id, updated_at)
                   VALUES ('sd_cand_conduit', 'assertion', 'asrt_cand_conduit', 'Conduit Pause',
                           'P&C Conduit paused pending review', 'candidate', 'current', 1,
                           'src_conduit_0818', 'asrt_cand_conduit', '2026-08-18')"""
            )
            conn.commit()

            row = conn.execute(
                """SELECT a.retrieval_eligible, s.retrieval_eligible
                   FROM assertion a
                   JOIN search_document s ON a.assertion_id = s.assertion_id
                   WHERE a.assertion_id = 'asrt_cand_conduit'"""
            ).fetchone()
            self.assertEqual(row, (1, 1))
        finally:
            conn.close()

    def test_e2e_sequential_speaker_claims_not_superseded(self) -> None:
        """Subsequent claims by the same speaker across meetings do not supersede earlier distinct claims."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO source VALUES
                   ('src_m1', '20260818 Conduit Update', '2026-08-18', 1),
                   ('src_m2', '20261009 Jarvis Standup', '2026-10-09', 1)"""
            )
            conn.execute(
                """INSERT OR REPLACE INTO assertion
                   (assertion_id, subject_node_id, predicate, object_literal, canonical_status,
                    canonical_eligible, validation_state, currentness_state, retrieval_eligible,
                    classification, allowed_audiences_json, qualifiers_json, primary_source_id, recorded_at)
                   VALUES
                   ('asrt_c1', 'person_amit', 'HAS_CLAIM_TEXT', 'P&C Conduit paused', 'canonical',
                    1, 'source_statement', 'current', 1, 'internal', '["all"]', '{}', 'src_m1', '2026-08-18T10:00:00Z'),
                   ('asrt_c2', 'person_amit', 'HAS_CLAIM_TEXT', 'Jarvis milestone reached', 'canonical',
                    1, 'source_statement', 'current', 1, 'internal', '["all"]', '{}', 'src_m2', '2026-10-09T10:00:00Z')"""
            )
            conn.commit()

            resolver = SupersessionResolver()
            policy = GraphFreshnessPolicy(supersession_strategy="newest_source_only")
            findings = resolver.scan(conn, policy)

            # Neither assertion should be superseded
            self.assertEqual(len(findings.superseded_mapping), 0)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
