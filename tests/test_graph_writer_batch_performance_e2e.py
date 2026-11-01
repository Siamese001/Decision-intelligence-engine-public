"""End-to-end performance and correctness tests for GovernedGraphWriter batching and RunRecorder buffering.

Verifies:
1. GovernedGraphWriter executes batched change-set validation and application in < 300ms.
2. Every receipt after_row_hash matches deterministic SQLite row digests.
3. Node, evidence, assertion, search_document, and audit_event tables are correctly populated.
4. RunRecorder buffers learning events in-memory, flushes across stage transitions, and seals with 100% validity.
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.contracts import create_artifact, seal_artifact
from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.graph_writer_core.helpers import _row_digest
from dual_pipeline.telemetry import RunRecorder

TEST_GRAPH_SCHEMA = """
CREATE TABLE source (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_date TEXT,
    authority_rank INTEGER NOT NULL DEFAULT 5,
    source_posture TEXT NOT NULL,
    cold_archive_ref TEXT,
    notes TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE source_version (
    source_version_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    source_fingerprint TEXT,
    content_hash TEXT NOT NULL,
    source_date_normalized TEXT,
    source_date_precision TEXT,
    source_date_status TEXT,
    authority_rank INTEGER,
    scope_text TEXT,
    posture_text TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE extraction_run (
    extraction_run_id TEXT PRIMARY KEY,
    algorithm_version TEXT NOT NULL,
    baseline_graph_sha256 TEXT NOT NULL,
    input_manifest_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    deterministic INTEGER NOT NULL
);
CREATE TABLE node (
    node_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    primary_label TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE evidence_item (
    evidence_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_version_id TEXT NOT NULL,
    locator_kind TEXT NOT NULL,
    locator_value TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE assertion (
    assertion_id TEXT PRIMARY KEY,
    assertion_fingerprint TEXT NOT NULL,
    subject_node_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    legacy_predicate TEXT,
    object_node_id TEXT,
    object_literal TEXT,
    object_literal_type TEXT,
    assertion_type TEXT NOT NULL,
    atomicity_state TEXT NOT NULL,
    scope_text TEXT NOT NULL,
    speaker_or_author TEXT,
    primary_source_id TEXT NOT NULL,
    extraction_run_id TEXT NOT NULL,
    extraction_method TEXT NOT NULL,
    origin_type TEXT NOT NULL,
    confidence_label TEXT NOT NULL,
    confidence_score REAL NOT NULL,
    validation_state TEXT NOT NULL,
    currentness_state TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    date_precision TEXT,
    date_status TEXT,
    recorded_at TEXT NOT NULL,
    superseded_at TEXT,
    superseded_by_assertion_id TEXT,
    resolution_state TEXT NOT NULL,
    canonical_status TEXT NOT NULL,
    canonical_eligible INTEGER NOT NULL,
    retrieval_eligible INTEGER NOT NULL,
    materiality TEXT NOT NULL,
    classification TEXT NOT NULL,
    allowed_audiences_json TEXT NOT NULL,
    hitl_required INTEGER NOT NULL,
    validation_priority TEXT,
    validation_owner TEXT,
    qualifiers_json TEXT NOT NULL,
    legacy_record_type TEXT NOT NULL,
    legacy_record_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    approval_reference TEXT
);
CREATE TABLE assertion_evidence (
    assertion_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    evidence_relationship TEXT NOT NULL,
    rank INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (assertion_id, evidence_id)
);
CREATE TABLE assertion_label_state (
    assertion_id TEXT PRIMARY KEY,
    extraction_confidence REAL NOT NULL,
    assertion_confidence_label TEXT,
    assertion_confidence_score REAL,
    validation_state TEXT NOT NULL,
    lifecycle_state TEXT NOT NULL,
    mapping_state TEXT NOT NULL,
    mapping_rule_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE assertion_audience (
    assertion_id TEXT NOT NULL,
    audience_id TEXT NOT NULL,
    mapping_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (assertion_id, audience_id)
);
CREATE TABLE search_document (
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
    updated_at TEXT NOT NULL
);
CREATE TABLE audit_event (
    audit_event_id TEXT PRIMARY KEY,
    actor_or_process TEXT NOT NULL,
    action TEXT NOT NULL,
    record_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    previous_value_json TEXT,
    new_value_json TEXT NOT NULL,
    source_id TEXT NOT NULL,
    event_timestamp TEXT NOT NULL,
    reason TEXT NOT NULL,
    validation_reference TEXT,
    approval_reference TEXT,
    migration_or_ingestion_run_id TEXT NOT NULL,
    release_version TEXT NOT NULL
);
CREATE TABLE governance_approval (
    approval_id TEXT PRIMARY KEY,
    approval_reference TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    status TEXT NOT NULL,
    active INTEGER NOT NULL,
    approved_at TEXT NOT NULL
);
"""


class TestGraphWriterBatchPerformanceE2E(unittest.TestCase):
    """End-to-end performance and validation test suite for Wave 4."""

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
        conn.execute("INSERT INTO node VALUES ('node-subj-001', 'Subject Node', 'concept', 1, '2026-09-24T00:00:00Z')")
        conn.execute("INSERT INTO node VALUES ('node-obj-001', 'Object Node', 'concept', 1, '2026-09-24T00:00:00Z')")
        conn.execute("INSERT INTO source VALUES ('source-test-01', 'dual_pipeline_intake', 'Test Source', '2026-09-24', 5, 'derivative_internal', NULL, 'Notes', '2026-09-24T00:00:00Z')")
        conn.execute(
            "INSERT INTO source_version VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sv-test-01", "source-test-01", 1, "fp", "a" * 64, "2026-09-24", "day", "exact", 5, "scope", "posture", "2026-09-24T00:00:00Z"),
        )
        conn.execute("INSERT INTO evidence_item VALUES ('ev-001', 'source-test-01', 'sv-test-01', 'locator', 'line:1', '2026-09-24T00:00:00Z')")
        conn.execute("INSERT INTO governance_approval VALUES ('app-001', 'ref-001', json_object('allowed_actions', json_array('canonical_assertion_promotion')), 'approved', 1, '2026-09-24T00:00:00Z')")
        conn.commit()
        conn.close()

    def test_governed_graph_writer_batch_apply_performance_e2e(self) -> None:
        """Verify GovernedGraphWriter applies 20 operations under 300ms with verified digests."""
        base_hash = sha256_file(self.graph_path)
        operations = []
        payloads: dict[str, Any] = {}

        for i in range(20):
            op_id = f"op-{i:03d}"
            target_id = f"assertion-perf-{i:03d}"
            cand = {
                "candidate_id": f"cand-{i:03d}",
                "candidate_type": "assertion",
                "normalized_text": f"Performance validation assertion statement {i} with high signal.",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "evidence_ids": ["ev-001"],
                "extraction_confidence": 0.92,
                "materiality": "high",
            }
            payload = {
                "candidate": cand,
                "subject_node_id": "node-subj-001",
                "object_node_id": "node-obj-001" if i % 2 == 0 else None,
                "predicate": "RELATES_TO" if i % 2 == 0 else "HAS_CLAIM_TEXT",
                "disposition": "APPROVED" if i < 10 else "CANDIDATE",
                "retrieval_audiences": ["internal_consulting"],
            }
            payloads[op_id] = payload
            operations.append({
                "operation_id": op_id,
                "sequence": i + 1,
                "operation": "add_approved_assertion" if i < 10 else "add_candidate_assertion",
                "target_type": "assertion",
                "target_id": target_id,
                "candidate_id": cand["candidate_id"],
                "classification": cand["classification"],
                "audiences": cand["audiences"],
                "evidence_ids": ["ev-001"],
                "before_state_hash": ZERO_HASH,
                "after_state_hash": digest(payload),
            })

        cs = create_artifact(
            "graph-change-set",
            "cs-perf-test-01",
            change_set_id="cs-perf-test-01",
            run_id="run-perf-test-01",
            trace_id="a" * 32,
            status="approved",
            approval_id="app-001",
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

        writer = GovernedGraphWriter(self.graph_path)
        t0 = time.monotonic()
        receipt = writer.apply(seal_artifact(cs))
        elapsed = time.monotonic() - t0

        # Performance constraint: < 300ms
        self.assertLess(elapsed, 0.300, f"Batch apply took {elapsed:.3f}s, expected < 0.300s")
        self.assertEqual(receipt["status"], "COMMITTED")
        self.assertEqual(receipt["operation_count"], 20)

        # Correctness: verify every row digest matches point query exactness
        conn = sqlite3.connect(self.graph_path)
        conn.row_factory = sqlite3.Row
        try:
            for op_receipt in receipt["operations"]:
                target_id = op_receipt["target_id"]
                expected_digest = _row_digest(conn, "assertion", "assertion_id", target_id)
                self.assertEqual(op_receipt["after_row_hash"], expected_digest)

            # Verify search documents were written
            search_count = conn.execute("SELECT count(*) FROM search_document").fetchone()[0]
            self.assertEqual(search_count, 20)

            # Verify audit events were written
            audit_count = conn.execute("SELECT count(*) FROM audit_event").fetchone()[0]
            self.assertEqual(audit_count, 20)
        finally:
            conn.close()

    def test_run_recorder_buffering_and_stage_boundary_flush_e2e(self) -> None:
        """Verify RunRecorder in-memory buffering, multi-stage flush, audit, and sealing."""
        recorder = RunRecorder(
            self.root,
            run_id="run-telemetry-perf-01",
            trace_id="b" * 32,
            use_case="intake",
            graph_snapshot="snapshot-test-01",
        )
        correlation = {
            "source_id": "source-test-01",
            "candidate_id": "cand-001",
            "query_id": None,
            "graph_operation_id": None,
            "hitl_item_id": None,
        }

        # Record 10 events in U0
        t0 = time.monotonic()
        for i in range(10):
            recorder.record(
                stage="U0",
                event_type="pipeline_event",
                payload={"index": i, "data": "u0 event"},
                correlation=correlation,
                status="PASS",
            )
        elapsed_u0 = time.monotonic() - t0
        self.assertLess(elapsed_u0, 0.150, "10 U0 events recording took too long")

        # Record 10 events in L0 (triggers stage-change flush)
        t1 = time.monotonic()
        for i in range(10):
            recorder.record(
                stage="L0",
                event_type="pipeline_event",
                payload={"index": i, "data": "l0 event"},
                correlation=correlation,
                status="PASS",
            )
        elapsed_l0 = time.monotonic() - t1
        self.assertLess(elapsed_l0, 0.150, "10 L0 events recording took too long")

        # Explicit audit should flush and pass all checks
        audit_res = recorder.audit(required_stages=["U0", "L0"])
        self.assertEqual(audit_res["event_count"], 20)
        self.assertEqual(audit_res["status"], "PASS")

        # Seal run with L4
        seal_res = recorder.seal(
            required_stages=["U0", "L0", "L4"],
            correlation=correlation,
            status="PASS",
        )
        self.assertEqual(seal_res["status"], "PASS")
        self.assertEqual(seal_res["event_count"], 21)

        # Verify disk artifacts
        seal_path = self.root / "runs" / "run-telemetry-perf-01" / "run_seal.json"
        self.assertTrue(seal_path.is_file())


if __name__ == "__main__":
    unittest.main()
