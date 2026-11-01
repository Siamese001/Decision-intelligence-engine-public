#!/usr/bin/env python3
"""Adversarial Test Suite for Wave 1 Mutation Engine Hardening.

Covers:
1. Concurrency races: Multi-worker concurrent transactions against the same SQLite database
   verifying GLOBAL_WRITE_SERIALIZER prevents database locks and corruption.
2. Invariant bypass attempts: Positive change gate enforcement when mutation operations
   produce identical before/after hash (zero-delta fail-closed).
3. Malformed inputs & parameter boundary validation in UWG process_commit_request.
4. Prepare crash simulation & cleanup: Atomic rollback on failure leaving clean filesystem.
"""
from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from dual_pipeline.contracts import create_artifact, seal_artifact
from dual_pipeline.core import ZERO_HASH, connect_sqlite, digest, sha256_file, write_json
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.release import ReleaseManager
from dual_pipeline.uwg import UniversalWriteGateway

TEST_SCHEMA = """
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


@pytest.mark.governance
class Wave1MutationAdversarialTests(unittest.TestCase):
    """Adversarial and stress test cases for Wave 1 mutation engine hardening."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.graph_path = self.root / "adversarial_graph.sqlite"
        self._init_database()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _init_database(self) -> None:
        conn = connect_sqlite(self.graph_path)
        conn.executescript(TEST_SCHEMA)
        conn.execute("INSERT INTO node VALUES ('node-root', 'Root System', 'system', 1, '2026-09-27T00:00:00Z')")
        for i in range(1, 10):
            conn.execute(f"INSERT INTO node VALUES ('node-{i}', 'Component {i}', 'module', 1, '2026-09-27T00:00:00Z')")
        conn.execute("INSERT INTO source VALUES ('source-adv-01', 'dual_pipeline_intake', 'Adversarial Source', '2026-09-27', 8, 'governed_internal', NULL, 'Notes', '2026-09-27T00:00:00Z')")
        conn.execute(
            "INSERT INTO source_version VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("sv-adv-01", "source-adv-01", 1, "fp", "c" * 64, "2026-09-27", "day", "exact", 8, "scope", "posture", "2026-09-27T00:00:00Z"),
        )
        conn.execute("INSERT INTO evidence_item VALUES ('ev-adv-001', 'source-adv-01', 'sv-adv-01', 'locator', 'line:1', '2026-09-27T00:00:00Z')")
        conn.execute("INSERT INTO governance_approval VALUES ('app-adv-001', 'ref-adv-001', json_object('allowed_actions', json_array('canonical_assertion_promotion')), 'approved', 1, '2026-09-27T00:00:00Z')")
        conn.commit()
        conn.close()

    def _make_change_set(self, op_id: str, assertion_id: str, target_node: str, run_id: str) -> dict[str, Any]:
        base_hash = sha256_file(self.graph_path)
        cand = {
            "candidate_id": f"cand-{assertion_id}",
            "candidate_type": "assertion",
            "normalized_text": f"System integrates with {target_node}",
            "classification": "internal",
            "audiences": ["engineering"],
            "evidence_ids": ["ev-adv-001"],
            "extraction_confidence": 0.95,
            "materiality": "high",
        }
        payload = {
            "candidate": cand,
            "subject_node_id": "node-root",
            "predicate": "INTEGRATES_WITH",
            "object_node_id": target_node,
            "disposition": "CANDIDATE",
            "retrieval_audiences": ["engineering"],
        }
        op = {
            "operation_id": op_id,
            "sequence": 1,
            "operation": "add_candidate_assertion",
            "target_type": "assertion",
            "target_id": assertion_id,
            "candidate_id": f"cand-{assertion_id}",
            "classification": "internal",
            "audiences": ["engineering"],
            "evidence_ids": ["ev-adv-001"],
            "before_state_hash": ZERO_HASH,
            "after_state_hash": digest(payload),
        }
        source = {
            "source_id": "source-adv-01",
            "source_version_id": "sv-adv-01",
            "name": "Adversarial Source",
            "package_digest": "c" * 64,
            "authority_rank": 8,
            "source_type": "governed_internal",
        }
        change_set = create_artifact(
            "graph-change-set",
            f"cs-{op_id}",
            change_set_id=f"cs-{op_id}",
            run_id=run_id,
            trace_id="a" * 32,
            approval_id="app-adv-001",
            status="approved",
            base_graph_snapshot="snapshot-adv-01",
            base_graph_hash=base_hash,
            transaction_required=True,
            rollback_required=True,
            operations=[op],
            extensions={
                "com.partner_advisory.nee.source": source,
                "com.partner_advisory.nee.operation-payloads": {op_id: payload},
            },
        )
        return seal_artifact(change_set)

    def test_adversarial_concurrency_race_condition(self) -> None:
        """Adversarial stress test: Multiple threads mutating the same SQLite graph concurrently.

        Verifies that GLOBAL_WRITE_SERIALIZER safely serializes all writes,
        preventing 'sqlite3.OperationalError: database is locked', while
        optimistic base-hash concurrency controls cleanly serialize updates.
        """
        worker_count = 5
        writer = GovernedGraphWriter(self.graph_path)

        def worker_task(worker_idx: int) -> dict[str, Any]:
            max_retries = 10
            for attempt in range(max_retries):
                cs = self._make_change_set(
                    op_id=f"op-conc-{worker_idx}-{attempt}",
                    assertion_id=f"as-conc-{worker_idx}",
                    target_node=f"node-{worker_idx}",
                    run_id=f"run-conc-{worker_idx}-{attempt}",
                )
                try:
                    return writer.apply(cs)
                except RuntimeError as exc:
                    if "does not match staging graph" in str(exc) and attempt < max_retries - 1:
                        continue
                    raise
            raise RuntimeError("Exhausted retries")

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(worker_task, i) for i in range(1, worker_count + 1)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(worker_count, len(results))
        for res in results:
            self.assertEqual("COMMITTED", res["status"])

        # Verify all 5 assertions exist in database
        conn = connect_sqlite(self.graph_path)
        count = conn.execute("SELECT count(*) FROM assertion WHERE assertion_id LIKE 'as-conc-%'").fetchone()[0]
        conn.close()
        self.assertEqual(worker_count, count)

    def test_adversarial_zero_delta_hash_collision_fails_closed(self) -> None:
        """Adversarial invariant test: Mutation that produces zero SHA-256 change must fail closed."""
        writer = GovernedGraphWriter(self.graph_path)
        cs = self._make_change_set(
            op_id="op-hash-01",
            assertion_id="as-hash-01",
            target_node="node-1",
            run_id="run-hash-01",
        )

        # Patch sha256_file to simulate a hash collision where after_hash == before_hash
        real_sha256 = sha256_file(self.graph_path)
        with patch("dual_pipeline.graph_writer_core.writer.sha256_file", return_value=real_sha256):
            with self.assertRaises(RuntimeError) as ctx:
                writer.apply(cs)
            self.assertIn("did not produce a changed graph hash", str(ctx.exception))

    def test_adversarial_uwg_rejects_malformed_commit_payloads(self) -> None:
        """Adversarial API test: UniversalWriteGateway rejects malformed and partial commit requests."""
        runtime_dir = self.root / "runtime"
        uwg = UniversalWriteGateway(runtime_dir, self.graph_path)

        # Case 1: Missing all paths raises ValueError
        with self.assertRaises(ValueError) as ctx:
            uwg.process_commit_request({"reviewer": "admin"})
        self.assertIn("requires either 'receipt_path'", str(ctx.exception))

        # Case 2: evaluation_path provided without decision_path raises ValueError
        with self.assertRaises(ValueError) as ctx:
            uwg.process_commit_request({
                "evaluation_path": "/tmp/eval.json",
                "reviewer": "admin",
            })
        self.assertIn("requires either 'receipt_path'", str(ctx.exception))

        # Case 3: Mock payload fails closed with MOCK_SYNTHESIS_NOT_AUTHORITATIVE
        with self.assertRaises(RuntimeError) as ctx:
            uwg.process_commit_request({"is_mock": True})
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))

        # Case 4: Non-existent approval request path raises error
        with self.assertRaises(Exception):
            uwg.process_commit_request({
                "approval_request_path": str(self.root / "nonexistent.json"),
                "reviewer": "admin",
            })

    def test_adversarial_mid_prepare_crash_leaves_clean_runtime(self) -> None:
        """Adversarial failure injection: Prepare failure cleans up orphan generation directory."""
        runtime_dir = self.root / "runtime"
        rm = ReleaseManager(runtime_dir, self.graph_path)

        active_gen_dir = runtime_dir / "generations" / "gen-seed"
        active_gen_dir.mkdir(parents=True)
        active_graph = active_gen_dir / "graph.sqlite"
        active_sidecar = active_gen_dir / "sidecar.sqlite"
        active_graph.write_bytes(self.graph_path.read_bytes())
        active_sidecar.touch()

        active_pair = {
            "schema_version": "1.0.0",
            "generation_id": "gen-seed",
            "graph_snapshot": "snapshot-seed",
            "graph_sha256": sha256_file(active_graph),
            "sidecar_sha256": sha256_file(active_sidecar),
            "graph_path": str(active_graph.relative_to(runtime_dir)),
            "sidecar_path": str(active_sidecar.relative_to(runtime_dir)),
            "embedding_backend": "flagembedding-bge-m3-dev",
            "model_id": "BAAI/bge-m3",
            "model_revision": "123456",
            "model_manifest_digest": "abcdef",
            "production_qualified": False,
            "activated_at": "2026-09-27T00:00:00Z",
        }
        write_json(rm.active_path, active_pair)

        state = MagicMock()
        state.active = active_pair
        state.backend = MagicMock(
            backend_id="flagembedding-bge-m3-dev",
            model_id="BAAI/bge-m3",
            model_revision="123456",
            model_manifest_digest="abcdef",
            retrieval_encoder="real-bge-m3",
            production_qualified=False,
        )
        state.approved = {"canonical_digest": {"value": "digest123"}}
        state.approved_components = [{"planned_graph_delta": {}}]
        state.fail_after_operations = None

        with patch("dual_pipeline.release_core.manager.exact_file_copy", side_effect=RuntimeError("Simulated preparation crash")):
            with self.assertRaises(RuntimeError) as ctx:
                rm._prepare_stage_execute_graph_and_sidecar(state)
            self.assertIn("Simulated preparation crash", str(ctx.exception))

        # Verify no orphan generation directory was left under generations/
        gen_dirs = list((runtime_dir / "generations").glob("generation-*"))
        self.assertEqual(0, len(gen_dirs))
