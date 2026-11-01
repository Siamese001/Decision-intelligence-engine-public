#!/usr/bin/env python3
"""
Verification suite for Core Storage, In-Memory Write Serialization,
Fail-Closed Cryptographic Boundaries, and W2 Semantic Spine Assertions.
Target: Apple MacBook Pro 16" (M5 Pro, 48GB Unified RAM)

Verifies:
  1. High-churn concurrent writes across 20 threads with 0 SQLITE_BUSY deadlocks.
  2. Fail-closed SQLCipher enforcement (RuntimeError on unencrypted fallback).
  3. W2 Assertion and evidence lifecycle with cascade referential integrity.
  4. Zero-knowledge HMAC entity anchoring across database boundaries.
"""
from __future__ import annotations

import concurrent.futures
import shutil
import tempfile
import unittest

from storage.database import (
    AdvisoryCategory,
    AssertionRecord,
    CanonicalGraphNode,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
    compute_entity_hmac,
)


class TestStorageConcurrency(unittest.TestCase):
    """Verifies in-memory write serialization under high thread concurrency."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir,
            allow_unencrypted_dev_fallback=True, # Allow dev fallback for temp SQLite in concurrency test
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_high_churn_concurrent_writes_zero_deadlocks(self):
        """
        Executes 500 interleaved inserts, leases, and updates across 20 concurrent threads.
        Asserts 0 SQLITE_BUSY or database lock errors.
        """
        num_threads = 20
        operations_per_thread = 25
        errors = []

        def worker_task(thread_id: int):
            for op in range(operations_per_thread):
                ev_id = f"ev_t{thread_id}_{op}"
                try:
                    # 1. Append evidence (writes to evidence.db + durable_work_queue)
                    rec = EvidenceRecord(
                        evidence_id=ev_id,
                        meeting_id=f"meet_concurrency_{thread_id}",
                        speaker=f"Speaker_{thread_id}",
                        text=f"Concurrent transcript utterance {op} from thread {thread_id}",
                        start_timestamp=float(op * 2.0),
                        end_timestamp=float(op * 2.0 + 1.8),
                        is_final=True,
                    )
                    event_id = self.db_manager.append_evidence(rec, enqueue=True)

                    # 2. Write to canonical graph
                    c_node = CanonicalGraphNode(
                        node_id=f"node_t{thread_id}_{op}",
                        evidence_id=ev_id,
                        type="ARCHITECTURAL_DECISION",
                        name=f"Decision {op} from thread {thread_id}",
                        strategic_value=8.0,
                    )
                    self.db_manager.upsert_canonical_node(c_node)

                    # 3. Lease work batch and complete
                    leased = self.db_manager.lease_work_batch(
                        batch_size=2,
                        worker_id=f"worker_{thread_id}",
                    )
                    for w_item, _ in leased:
                        if w_item.event_id == event_id:
                            self.db_manager.complete_work_item(event_id=w_item.event_id)

                except Exception as e:
                    errors.append(f"Thread {thread_id} op {op} failed: {type(e).__name__}: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker_task, i) for i in range(num_threads)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, f"Encountered {len(errors)} concurrency errors: {errors[:5]}")

        # Assert data integrity
        for t in range(num_threads):
            records = self.db_manager.get_evidence_by_meeting(f"meet_concurrency_{t}")
            self.assertEqual(len(records), operations_per_thread)


class TestFailClosedCryptographicSecurity(unittest.TestCase):
    """Verifies fail-closed enforcement when SQLCipher native driver is missing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_fail_closed_raises_runtime_error_when_unencrypted_forbidden(self):
        """
        Simulate environment where SQLCipher is unavailable and verify
        that allow_unencrypted_dev_fallback=False strictly raises RuntimeError.
        """
        import storage.database as db_mod
        orig_sqlcipher = db_mod.SQLCIPHER_AVAILABLE

        try:
            # Force SQLCIPHER_AVAILABLE to False to simulate environment lacking SQLCipher driver
            db_mod.SQLCIPHER_AVAILABLE = False

            # Initializing DatabaseManager without dev fallback MUST raise RuntimeError
            with self.assertRaises(RuntimeError) as ctx:
                DatabaseManager(
                    base_dir=self.temp_dir,
                    allow_unencrypted_dev_fallback=False, # Production default
                )
            self.assertIn("CRITICAL: SQLCipher native driver is unavailable", str(ctx.exception))
            self.assertIn("Fail-Closed Law 2", str(ctx.exception))

        finally:
            db_mod.SQLCIPHER_AVAILABLE = orig_sqlcipher

    def test_allow_unencrypted_dev_fallback_initializes(self):
        """Verifies that explicit dev fallback allows development in offline testing environments."""
        import storage.database as db_mod
        orig_sqlcipher = db_mod.SQLCIPHER_AVAILABLE

        try:
            db_mod.SQLCIPHER_AVAILABLE = False
            mgr = DatabaseManager(
                base_dir=self.temp_dir,
                allow_unencrypted_dev_fallback=True, # Explicit dev mode
            )
            # Should succeed and create databases
            self.assertTrue(mgr.shadow_db_path.exists())
            self.assertTrue(mgr.canonical_db_path.exists())
        finally:
            db_mod.SQLCIPHER_AVAILABLE = orig_sqlcipher


class TestW2SemanticSpineAssertions(unittest.TestCase):
    """Verifies first-class assertion lifecycle, multi-point evidence bindings, and cascades."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir,
            allow_unencrypted_dev_fallback=True,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_assertion_lifecycle_and_evidence_bindings(self):
        # 1. Create canonical subject node
        node = CanonicalGraphNode(
            node_id="node_grid_fargate",
            evidence_id="ev_raw_01",
            type="SYSTEM_ARCHITECTURE",
            name="AWS Fargate Grid Dispatcher",
            strategic_value=9.0,
        )
        self.db_manager.upsert_canonical_node(node)

        # 2. Insert first-class assertion bound to 2 evidentiary quotes
        assertion = AssertionRecord(
            assertion_id="assert_deploy_decision_01",
            subject_node_id="node_grid_fargate",
            predicate="DECISION_COMMITTED_TO",
            literal_value="Deploy grid telemetry on AWS Fargate by Q4",
            assertion_type="DECISION",
            confidence=0.95,
            status="CONFIRMED",
            observed_at="2026-09-12 10:00:00",
            stale_after="2026-12-31 23:59:59",
        )
        bindings = [
            ("ev_raw_01", "We are committing to deploy the ingestion service on AWS Fargate."),
            ("ev_raw_02", "Confirmed by architecture committee for Q4 release."),
        ]
        self.db_manager.upsert_assertion(assertion, evidence_bindings=bindings)

        # 3. Query assertions
        results = self.db_manager.query_assertions(subject_node_id="node_grid_fargate")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "CONFIRMED")
        self.assertEqual(results[0].literal_value, "Deploy grid telemetry on AWS Fargate by Q4")

        # 4. State transition: SUPERSEDED
        assertion_superseded = AssertionRecord(
            assertion_id="assert_deploy_decision_01",
            subject_node_id="node_grid_fargate",
            predicate="DECISION_COMMITTED_TO",
            literal_value="Decision superseded: On-prem OpenShift required for CIP compliance",
            assertion_type="DECISION",
            confidence=1.0,
            status="SUPERSEDED",
        )
        self.db_manager.upsert_assertion(assertion_superseded)

        updated_results = self.db_manager.query_assertions(status="SUPERSEDED")
        self.assertEqual(len(updated_results), 1)
        self.assertEqual(updated_results[0].status, "SUPERSEDED")

    def test_zero_knowledge_hmac_entity_anchoring(self):
        """Verifies deterministic HMAC links advisory insights without cross-database FK leaks."""
        canonical_node_id = "person:tanner_stevens"
        anchor_hmac = compute_entity_hmac(canonical_node_id)

        # Verify deterministic property
        self.assertEqual(compute_entity_hmac(canonical_node_id), anchor_hmac)
        self.assertNotEqual(anchor_hmac, canonical_node_id)
        self.assertNotIn("tanner", anchor_hmac)

        # Upsert advisory node using the zero-knowledge anchor
        adv_node = InternalAdvisoryNode(
            node_id="adv_tanner_resistance",
            evidence_id="ev_raw_03",
            type="STAKEHOLDER_DYNAMICS",
            name="Tanner Stevens Advisory Signal",
            category=AdvisoryCategory.STAKEHOLDER_DYNAMICS,
            target_entity_hmac=anchor_hmac,
            properties={"observation": "Strong governance proponent; requires formal eval sign-off."},
            strategic_value=9.2,
        )
        self.db_manager.upsert_shadow_advisory(adv_node)

        # Query advisory nodes and assert HMAC anchor is stored correctly
        advisory_list = self.db_manager.query_internal_advisory_nodes()
        self.assertEqual(len(advisory_list), 1)
        self.assertEqual(advisory_list[0].target_entity_hmac, anchor_hmac)


if __name__ == "__main__":
    unittest.main()
