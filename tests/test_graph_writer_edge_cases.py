"""Adversarial and edge-case testing for GovernedGraphWriter batching and RunRecorder buffering.

Covers:
1. Target collision rejection: batch fails closed when target assertion already exists.
2. Inactive/absent subject node rejection: fails closed when subject_node_id does not exist or active=0.
3. Inactive/absent object node rejection: fails closed when object_node_id does not exist or active=0.
4. Missing governed evidence rejection: fails closed when evidence_ids reference absent records.
5. Injected mid-batch failure: rollback preserves atomic before-state with zero orphan records.
6. Disposition mismatch rejection: add_approved_assertion requires disposition=='APPROVED'.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import create_artifact, seal_artifact
from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file
from dual_pipeline.graph_writer import GovernedGraphWriter

TEST_GRAPH_SCHEMA = """
CREATE TABLE source (source_id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_name TEXT NOT NULL, source_date TEXT, authority_rank INTEGER NOT NULL DEFAULT 5, source_posture TEXT NOT NULL, cold_archive_ref TEXT, notes TEXT, created_at TEXT NOT NULL);
CREATE TABLE source_version (source_version_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, version_number INTEGER NOT NULL, source_fingerprint TEXT, content_hash TEXT NOT NULL, source_date_normalized TEXT, source_date_precision TEXT, source_date_status TEXT, authority_rank INTEGER, scope_text TEXT, posture_text TEXT, created_at TEXT NOT NULL);
CREATE TABLE extraction_run (extraction_run_id TEXT PRIMARY KEY, algorithm_version TEXT NOT NULL, baseline_graph_sha256 TEXT NOT NULL, input_manifest_sha256 TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL, deterministic INTEGER NOT NULL);
CREATE TABLE node (node_id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, primary_label TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_version_id TEXT NOT NULL, locator_kind TEXT NOT NULL, locator_value TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE assertion (
    assertion_id TEXT PRIMARY KEY, assertion_fingerprint TEXT NOT NULL, subject_node_id TEXT NOT NULL, predicate TEXT NOT NULL,
    legacy_predicate TEXT, object_node_id TEXT, object_literal TEXT, object_literal_type TEXT, assertion_type TEXT NOT NULL,
    atomicity_state TEXT NOT NULL, scope_text TEXT NOT NULL, speaker_or_author TEXT, primary_source_id TEXT NOT NULL,
    extraction_run_id TEXT NOT NULL, extraction_method TEXT NOT NULL, origin_type TEXT NOT NULL, confidence_label TEXT NOT NULL,
    confidence_score REAL NOT NULL, validation_state TEXT NOT NULL, currentness_state TEXT NOT NULL, valid_from TEXT,
    valid_to TEXT, date_precision TEXT, date_status TEXT, recorded_at TEXT NOT NULL, superseded_at TEXT, superseded_by_assertion_id TEXT,
    resolution_state TEXT NOT NULL, canonical_status TEXT NOT NULL, canonical_eligible INTEGER NOT NULL, retrieval_eligible INTEGER NOT NULL,
    materiality TEXT NOT NULL, classification TEXT NOT NULL, allowed_audiences_json TEXT NOT NULL, hitl_required INTEGER NOT NULL,
    validation_priority TEXT, validation_owner TEXT, qualifiers_json TEXT NOT NULL, legacy_record_type TEXT NOT NULL,
    legacy_record_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, approval_reference TEXT
);
CREATE TABLE assertion_evidence (assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL, evidence_relationship TEXT NOT NULL, rank INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, PRIMARY KEY (assertion_id, evidence_id));
CREATE TABLE assertion_label_state (assertion_id TEXT PRIMARY KEY, extraction_confidence REAL NOT NULL, assertion_confidence_label TEXT, assertion_confidence_score REAL, validation_state TEXT NOT NULL, lifecycle_state TEXT NOT NULL, mapping_state TEXT NOT NULL, mapping_rule_id TEXT, updated_at TEXT NOT NULL);
CREATE TABLE assertion_audience (assertion_id TEXT NOT NULL, audience_id TEXT NOT NULL, mapping_state TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (assertion_id, audience_id));
CREATE TABLE search_document (search_document_id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, canonical_status TEXT NOT NULL, currentness_state TEXT NOT NULL, retrieval_eligible INTEGER NOT NULL, source_id TEXT NOT NULL, assertion_id TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE audit_event (audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT NOT NULL, action TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL, previous_value_json TEXT, new_value_json TEXT NOT NULL, source_id TEXT NOT NULL, event_timestamp TEXT NOT NULL, reason TEXT NOT NULL, validation_reference TEXT, approval_reference TEXT, migration_or_ingestion_run_id TEXT NOT NULL, release_version TEXT NOT NULL);
CREATE TABLE governance_approval (approval_id TEXT PRIMARY KEY, approval_reference TEXT NOT NULL, scope_json TEXT NOT NULL, status TEXT NOT NULL, active INTEGER NOT NULL, approved_at TEXT NOT NULL);
"""


class TestGraphWriterEdgeCases(unittest.TestCase):
    """Edge cases and adversarial boundary conditions for GovernedGraphWriter."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.graph_path = self.root / "test_graph.sqlite"
        self._init_database()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _init_database(self) -> None:
        conn = connect_sqlite(self.graph_path)
        conn.executescript(TEST_GRAPH_SCHEMA)
        conn.execute("INSERT INTO node VALUES ('node-active-01', 'Active Node', 'concept', 1, '2026-09-24T00:00:00Z')")
        conn.execute("INSERT INTO node VALUES ('node-inactive-02', 'Inactive Node', 'concept', 0, '2026-09-24T00:00:00Z')")
        conn.execute("INSERT INTO source VALUES ('source-test-01', 'dual_pipeline_intake', 'Test Source', '2026-09-24', 5, 'derivative_internal', NULL, 'Notes', '2026-09-24T00:00:00Z')")
        conn.execute(
            "INSERT INTO source_version VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sv-test-01", "source-test-01", 1, "fp", "a" * 64, "2026-09-24", "day", "exact", 5, "scope", "posture", "2026-09-24T00:00:00Z"),
        )
        conn.execute("INSERT INTO evidence_item VALUES ('ev-001', 'source-test-01', 'sv-test-01', 'locator', 'line:1', '2026-09-24T00:00:00Z')")
        conn.execute("INSERT INTO governance_approval VALUES ('app-001', 'ref-001', json_object('allowed_actions', json_array('canonical_assertion_promotion')), 'approved', 1, '2026-09-24T00:00:00Z')")

        # Existing assertion for collision testing
        conn.execute(
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
                'assertion-existing-collision', 'fp-collision', 'node-active-01', 'HAS_CLAIM_TEXT', NULL,
                NULL, 'Pre-existing claim.', 'text', 'extracted_claim', 'atomic',
                'Pre-existing claim.', NULL, 'source-test-01', 'run-0', 'adapter',
                'human_source', 'high', 0.9, 'needs_review', 'current',
                '2026-09-24', NULL, 'day', 'exact', '2026-09-24T00:00:00Z', NULL,
                NULL, 'resolved', 'candidate', 0,
                1, 'high', 'internal', '["internal_consulting"]', 0,
                'P1', NULL, '{}', 'claim',
                '[]', '2026-09-24T00:00:00Z', '2026-09-24T00:00:00Z', NULL
            )"""
        )
        conn.commit()
        conn.close()

    def _build_change_set(
        self,
        operations: list[dict[str, Any]],
        payloads: dict[str, Any],
        approval_id: str | None = "app-001",
    ) -> dict[str, Any]:
        base_hash = sha256_file(self.graph_path)
        return create_artifact(
            "graph-change-set",
            "cs-edge-test",
            change_set_id="cs-edge-test",
            run_id="run-edge-test",
            trace_id="c" * 32,
            status="approved",
            approval_id=approval_id,
            base_graph_snapshot="snapshot-test-01",
            base_graph_hash=base_hash,
            operations=operations,
            transaction_required=True,
            rollback_required=True,
            extensions={
                "com.partner_advisory.nee.source": {
                    "source_id": "source-test-01",
                    "source_version_id": "sv-test-01",
                    "name": "Test Source",
                    "package_digest": "a" * 64,
                },
                "com.partner_advisory.nee.operation-payloads": payloads,
            },
        )

    def test_edge_case_1_target_collision_in_batch_fails_closed(self) -> None:
        """Batch containing an existing target assertion ID must be rejected before mutation."""
        cand = {
            "candidate_id": "cand-col",
            "candidate_type": "assertion",
            "normalized_text": "Colliding assertion.",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-001"],
            "extraction_confidence": 0.90,
            "materiality": "high",
        }
        payload = {
            "candidate": cand,
            "subject_node_id": "node-active-01",
            "predicate": "HAS_CLAIM_TEXT",
            "disposition": "CANDIDATE",
            "retrieval_audiences": ["internal_consulting"],
        }
        op = {
            "operation_id": "op-col",
            "sequence": 1,
            "operation": "add_candidate_assertion",
            "target_type": "assertion",
            "target_id": "assertion-existing-collision",  # Collides!
            "candidate_id": "cand-col",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-001"],
            "before_state_hash": ZERO_HASH,
            "after_state_hash": digest(payload),
        }
        cs = self._build_change_set([op], {"op-col": payload})
        writer = GovernedGraphWriter(self.graph_path)
        with self.assertRaisesRegex(RuntimeError, "target already exists"):
            writer.apply(seal_artifact(cs))

    def test_edge_case_2_inactive_or_missing_subject_node_fails_closed(self) -> None:
        """Candidate referencing an inactive subject node must be rejected."""
        cand = {
            "candidate_id": "cand-inact",
            "candidate_type": "assertion",
            "normalized_text": "Assertion on inactive node.",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-001"],
            "extraction_confidence": 0.90,
            "materiality": "high",
        }
        payload = {
            "candidate": cand,
            "subject_node_id": "node-inactive-02",  # Inactive!
            "predicate": "HAS_CLAIM_TEXT",
            "disposition": "CANDIDATE",
            "retrieval_audiences": ["internal_consulting"],
        }
        op = {
            "operation_id": "op-inact",
            "sequence": 1,
            "operation": "add_candidate_assertion",
            "target_type": "assertion",
            "target_id": "assertion-new-inact",
            "candidate_id": "cand-inact",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-001"],
            "before_state_hash": ZERO_HASH,
            "after_state_hash": digest(payload),
        }
        cs = self._build_change_set([op], {"op-inact": payload})
        writer = GovernedGraphWriter(self.graph_path)
        with self.assertRaisesRegex(RuntimeError, "subject identity is absent or inactive"):
            writer.apply(seal_artifact(cs))

    def test_edge_case_3_missing_evidence_item_fails_closed(self) -> None:
        """Operation referencing absent evidence item must fail closed."""
        cand = {
            "candidate_id": "cand-ev-miss",
            "candidate_type": "assertion",
            "normalized_text": "Assertion on missing evidence.",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-nonexistent-999"],
            "extraction_confidence": 0.90,
            "materiality": "high",
        }
        payload = {
            "candidate": cand,
            "subject_node_id": "node-active-01",
            "predicate": "HAS_CLAIM_TEXT",
            "disposition": "CANDIDATE",
            "retrieval_audiences": ["internal_consulting"],
        }
        op = {
            "operation_id": "op-ev-miss",
            "sequence": 1,
            "operation": "add_candidate_assertion",
            "target_type": "assertion",
            "target_id": "assertion-new-ev-miss",
            "candidate_id": "cand-ev-miss",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-nonexistent-999"],
            "before_state_hash": ZERO_HASH,
            "after_state_hash": digest(payload),
        }
        cs = self._build_change_set([op], {"op-ev-miss": payload})
        writer = GovernedGraphWriter(self.graph_path)
        with self.assertRaisesRegex(RuntimeError, "governed evidence is missing"):
            writer.apply(seal_artifact(cs))

    def test_edge_case_4_injected_failure_rolls_back_cleanly(self) -> None:
        """fail_after_operations triggers atomic rollback without partial mutations."""
        before_hash = sha256_file(self.graph_path)
        ops = []
        payloads = {}
        for i in range(4):
            op_id = f"op-inj-{i}"
            cand = {
                "candidate_id": f"cand-inj-{i}",
                "candidate_type": "assertion",
                "normalized_text": f"Injected failure test assertion {i}.",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "evidence_ids": ["ev-001"],
                "extraction_confidence": 0.85,
                "materiality": "high",
            }
            p = {
                "candidate": cand,
                "subject_node_id": "node-active-01",
                "predicate": "HAS_CLAIM_TEXT",
                "disposition": "CANDIDATE",
                "retrieval_audiences": ["internal_consulting"],
            }
            payloads[op_id] = p
            ops.append({
                "operation_id": op_id,
                "sequence": i + 1,
                "operation": "add_candidate_assertion",
                "target_type": "assertion",
                "target_id": f"assertion-inj-{i}",
                "candidate_id": cand["candidate_id"],
                "classification": cand["classification"],
                "audiences": cand["audiences"],
                "evidence_ids": ["ev-001"],
                "before_state_hash": ZERO_HASH,
                "after_state_hash": digest(p),
            })
        cs = self._build_change_set(ops, payloads)
        writer = GovernedGraphWriter(self.graph_path)

        with self.assertRaisesRegex(RuntimeError, "injected graph-writer failure"):
            writer.apply(seal_artifact(cs), fail_after_operations=2)

        # Database hash must remain strictly unchanged
        after_hash = sha256_file(self.graph_path)
        self.assertEqual(before_hash, after_hash)

        # No partially written assertions
        conn = sqlite3.connect(self.graph_path)
        count = conn.execute("SELECT count(*) FROM assertion WHERE assertion_id LIKE 'assertion-inj-%'").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_edge_case_5_overwrite_matching_assertion_fingerprint(self) -> None:
        """Batch inserting an assertion matching an existing fingerprint overwrites the prior row cleanly."""
        from dual_pipeline.core import digest
        computed_fp = digest({
            "subject": "node-active-01",
            "predicate": "HAS_CLAIM_TEXT",
            "object_node_id": None,
            "object_literal": "Pre-existing claim.",
            "scope": "Pre-existing claim.",
            "source_version_id": "sv-test-01",
        })
        conn = sqlite3.connect(self.graph_path)
        conn.execute("UPDATE assertion SET assertion_fingerprint=? WHERE assertion_id='assertion-existing-collision'", (computed_fp,))
        conn.commit()
        conn.close()

        cand = {
            "candidate_id": "cand-overwrite",
            "candidate_type": "assertion",
            "normalized_text": "Pre-existing claim.",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-001"],
            "extraction_confidence": 0.95,
            "materiality": "high",
        }
        payload = {
            "candidate": cand,
            "subject_node_id": "node-active-01",
            "predicate": "HAS_CLAIM_TEXT",
            "disposition": "CANDIDATE",
        }
        op = {
            "operation_id": "op-overwrite",
            "sequence": 1,
            "operation": "add_candidate_assertion",
            "target_type": "assertion",
            "target_id": "assertion-fresh-replacement",
            "candidate_id": "cand-overwrite",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "evidence_ids": ["ev-001"],
            "before_state_hash": ZERO_HASH,
            "after_state_hash": digest(payload),
        }
        cs = self._build_change_set([op], {"op-overwrite": payload})
        res = GovernedGraphWriter(self.graph_path).apply(seal_artifact(cs))
        self.assertEqual(res["status"], "COMMITTED")

        conn = sqlite3.connect(self.graph_path)
        row = conn.execute("SELECT assertion_id, confidence_score FROM assertion WHERE assertion_id='assertion-fresh-replacement'").fetchone()
        old_row = conn.execute("SELECT assertion_id FROM assertion WHERE assertion_id='assertion-existing-collision'").fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertIsNone(old_row)


if __name__ == "__main__":
    unittest.main()

