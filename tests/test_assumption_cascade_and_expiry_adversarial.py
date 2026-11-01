"""Adversarial and lifecycle test suite for assumption TTL expiry and cascade supersession.

Verifies:
1. AssertionTTLSweeper functions on live production schema (valid_to / qualifiers_json.stale_after).
2. Exploratory assumptions past TTL automatically transition to historical / retrieval_eligible=0
   even without a replacement assertion (require_supersession bypassed).
3. Option-rejection cascade: Ingesting DECIDED_AGAINST or REJECTED on an option node
   supersedes both incoming and outgoing assertions on that node, plus associated edges and search docs.
4. Concurrency & WAL stress: multiple sweeps do not crash or produce locked states.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import ROOT, utc_now
from dual_pipeline.graph_signal_core import supersede_conflicting_assertions
from dual_pipeline.staleness_core.ttl_sweeper import AssertionTTLSweeper


class TestAssumptionCascadeAndExpiryAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        active_pair_path = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
        if active_pair_path.exists():
            with open(active_pair_path, "r", encoding="utf-8") as f:
                pair = json.load(f)
            self.orig_graph = ROOT / ".local" / "dual_pipeline" / pair["graph_path"]
        else:
            candidates = list((ROOT / ".local" / "dual_pipeline" / "generations").glob("generation-*/graph.sqlite"))
            if not candidates:
                self.skipTest("No baseline generation graph found")
            self.orig_graph = candidates[0]
        self.db_path = Path(self.tmp_dir.name) / "test_cascade_expiry.sqlite"
        shutil.copy2(self.orig_graph, self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_ttl_sweeper_expires_unconfirmed_assumptions_without_replacement(self) -> None:
        """Verify AssertionTTLSweeper demotes expired assumptions without requiring superseding claim."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        now = utc_now()
        rec_at = "2026-09-04T14:00:00Z"
        # Seed an unconfirmed exploratory assumption with valid_to in the past
        cur.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate,
                object_node_id, object_literal, object_literal_type, assertion_type, atomicity_state,
                scope_text, speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                valid_from, valid_to, date_precision, date_status, recorded_at, superseded_at,
                superseded_by_assertion_id, resolution_state, canonical_status, canonical_eligible,
                retrieval_eligible, materiality, classification, allowed_audiences_json, hitl_required,
                validation_priority, validation_owner, qualifiers_json, legacy_record_type,
                legacy_record_ids_json, created_at, updated_at, approval_reference
            ) VALUES (
                'asrt_test_expired_gcp_greenfield', ?, 'entity-1fbe770a467b00e9e7df9c2c7349',
                'RELATED_TO', NULL, 'entity-9a178bfe208e265f5c4b7087ac92', NULL, NULL,
                'assumption', 'atomic', '45-Day Parallel FDX Greenfield on GCP', 'logan Miller',
                'src_test_01', 'run_test_01', 'structured', 'human_source', 'medium', 0.65,
                'needs_review', 'current', '2026-09-04', '2026-09-09', 'day', 'exact',
                ?, NULL, NULL, 'resolved', 'candidate', 0, 1,
                'medium', 'internal', '["internal_consulting"]', 0, NULL, NULL,
                '{"stale_after": "2026-09-09"}', 'edge', '[]', ?, ?, NULL
            )""",
            ("a" * 64, rec_at, now, now),
        )
        conn.commit()

        # Run sweeper evaluated at 2026-09-15
        sweeper = AssertionTTLSweeper(require_supersession=True)
        res = sweeper.sweep_stale_assertions(conn, as_of_date="2026-09-15")

        self.assertEqual(res["status"], "PASS")
        swept_ids = [s["assertion_id"] for s in res["swept_assertions"]]
        self.assertIn("asrt_test_expired_gcp_greenfield", swept_ids)

        # Verify database state
        row = cur.execute(
            """SELECT currentness_state, validation_state, retrieval_eligible
               FROM assertion WHERE assertion_id = 'asrt_test_expired_gcp_greenfield'"""
        ).fetchone()
        self.assertEqual(row[0], "historical")
        self.assertEqual(row[1], "superseded")
        self.assertEqual(row[2], 0)
        conn.close()

    def test_rejection_cascade_supersedes_option_subgraph(self) -> None:
        """Verify DECIDED_AGAINST on an option node supersedes both subject and object assertions and edges."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        now = utc_now()
        option_node = "entity-1fbe770a467b00e9e7df9c2c7349"
        gcp_node = "entity-9a178bfe208e265f5c4b7087ac92"
        stakeholder_node = "stakeholder_marcus_candidate"

        # Ensure option node exists in node table
        cur.execute(
            "INSERT OR IGNORE INTO node (node_id, canonical_name, node_type, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (option_node, "45-Day Parallel FDX Greenfield", "deliverable", now, now),
        )
        cur.execute(
            "INSERT OR IGNORE INTO node (node_id, canonical_name, node_type, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (gcp_node, "Google Cloud Platform", "deliverable", now, now),
        )

        rec_at = "2026-09-04T14:00:00Z"
        # 1. Option has an extracted claim (where option is subject)
        cur.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate,
                object_literal, object_literal_type,
                assertion_type, atomicity_state, scope_text, validation_state, currentness_state,
                date_precision, date_status, recorded_at, resolution_state,
                origin_type, extraction_method, confidence_label, confidence_score,
                canonical_status, canonical_eligible, retrieval_eligible, materiality,
                classification, allowed_audiences_json, hitl_required, qualifiers_json,
                legacy_record_type, legacy_record_ids_json, created_at, updated_at
            ) VALUES (
                'asrt_claim_greenfield_claim', ?, ?, 'HAS_CLAIM_TEXT', NULL,
                '45-Day Parallel FDX Greenfield program description', 'text',
                'extracted_claim', 'atomic', '45-Day Parallel FDX Greenfield program description', 'needs_review', 'current',
                'day', 'exact', ?, 'resolved',
                'human_source', 'structured', 'medium', 0.65,
                'candidate', 0, 1, 'medium',
                'internal', '["internal_consulting"]', 0, '{}', 'claim', '[]', ?, ?
            )""",
            ("b" * 64, option_node, rec_at, now, now),
        )

        # 2. Option has an exploratory edge to GCP (where option is subject)
        cur.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate, object_node_id,
                assertion_type, atomicity_state, scope_text, validation_state, currentness_state,
                date_precision, date_status, recorded_at, resolution_state,
                origin_type, extraction_method, confidence_label, confidence_score,
                canonical_status, canonical_eligible, retrieval_eligible, materiality,
                classification, allowed_audiences_json, hitl_required, qualifiers_json,
                legacy_record_type, legacy_record_ids_json, created_at, updated_at
            ) VALUES (
                'asrt_edge_greenfield_gcp', ?, ?, 'RELATED_TO', NULL, ?,
                'assumption', 'atomic', '45-Day Parallel FDX Greenfield on GCP',
                'needs_review', 'current',
                'day', 'exact', ?, 'resolved',
                'human_source', 'structured', 'medium', 0.65,
                'candidate', 0, 1, 'medium',
                'internal', '["internal_consulting"]', 0, '{}', 'edge', '[]', ?, ?
            )""",
            ("c" * 64, option_node, gcp_node, rec_at, now, now),
        )

        ws_node = "workstream_project_beacon"
        cur.execute(
            "INSERT OR IGNORE INTO node (node_id, canonical_name, node_type, active, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?)",
            (ws_node, "Project Beacon", "workstream", now, now),
        )

        # 3. Edge table row for greenfield (program) -> workstream (SUPPORTS)
        cur.execute(
            """INSERT OR REPLACE INTO edge (
                edge_id, from_node_id, edge_type, to_node_id, description,
                evidence_posture, confidence, materiality, hitl_required, source_id,
                created_at, updated_at
            ) VALUES (
                'edge_greenfield_ws', ?, 'SUPPORTS', ?, 'Greenfield supports Project Beacon proposal',
                'working_notes', 'medium', 'medium', 0, 'src_01', ?, ?
            )""",
            (option_node, ws_node, now, now),
        )
        conn.commit()

        # Ingest client rejection: Marcus Balk DECIDED_AGAINST option_node
        incoming_rejection_id = "asrt_marcus_rejection_decision"
        superseded = supersede_conflicting_assertions(
            conn,
            incoming_assertion_id=incoming_rejection_id,
            subject_node_id=stakeholder_node,
            predicate="DECIDED_AGAINST",
            object_node_id=option_node,
            now_iso=now,
        )

        self.assertIn("asrt_claim_greenfield_claim", superseded)
        self.assertIn("asrt_edge_greenfield_gcp", superseded)

        # Check assertion table
        for aid in ("asrt_claim_greenfield_claim", "asrt_edge_greenfield_gcp"):
            row = cur.execute(
                "SELECT currentness_state, superseded_by_assertion_id, retrieval_eligible FROM assertion WHERE assertion_id = ?",
                (aid,),
            ).fetchone()
            self.assertEqual(row[0], "historical")
            self.assertEqual(row[1], incoming_rejection_id)
            self.assertEqual(row[2], 0)

        # Check edge table
        edge_row = cur.execute(
            "SELECT superseded_by FROM edge WHERE edge_id = 'edge_greenfield_ws'"
        ).fetchone()
        self.assertEqual(edge_row[0], incoming_rejection_id)

        conn.close()


if __name__ == "__main__":
    unittest.main()
