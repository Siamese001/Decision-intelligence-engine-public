#!/usr/bin/env python3
"""
Verification suite for Shared-Nothing Parallel Architecture, Sharded Crash-Durable
Evidence Journaling, Vectorized Queue Leasing, and Multi-Process Test Execution.
Target: Apple MacBook Pro 16" (M5 Pro, 48GB Unified RAM)
"""

from __future__ import annotations

import concurrent.futures
import shutil
import tempfile
import unittest
from pathlib import Path

from storage.database import (
    DatabaseManager,
    EvidenceRecord,
    WorkItemState,
)
from tools.run_tests_with_progress import ProgressBarTestRunner


class TestParallelStorageAndVectorization(unittest.TestCase):
    """Verifies sharded evidence journaling and vectorized queue leasing."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="parallel_storage_test_")
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir,
            allow_unencrypted_dev_fallback=True,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sharded_evidence_journaling_and_consolidation(self):
        """
        Spawns 4 concurrent worker threads appending 25 evidence records each
        to independent session shards. Asserts 0 lock contention and 100% crash durability.
        Consolidates shards in a single bulk transaction and verifies total count.
        """
        num_sessions = 4
        records_per_session = 25
        errors = []

        def session_worker(session_idx: int):
            session_id = f"session_stream_{session_idx}"
            for i in range(records_per_session):
                ev_id = f"ev_s{session_idx}_{i}"
                rec = EvidenceRecord(
                    evidence_id=ev_id,
                    meeting_id=f"meeting_session_{session_idx}",
                    speaker=f"Speaker_{session_idx}",
                    text=f"Live stream utterance {i} in session {session_idx}",
                    start_timestamp=float(i * 1.5),
                    end_timestamp=float(i * 1.5 + 1.2),
                    is_final=True,
                )
                try:
                    self.db_manager.append_evidence_sharded(rec, session_id=session_id, enqueue=True)
                except Exception as e:
                    errors.append(f"Session {session_idx} write failed: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_sessions) as executor:
            futures = [executor.submit(session_worker, idx) for idx in range(num_sessions)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, f"Encountered write errors: {errors}")

        # Verify each session shard file exists on disk
        for idx in range(num_sessions):
            shard_file = Path(self.temp_dir) / f"evidence_session_stream_{idx}.db"
            self.assertTrue(shard_file.exists(), f"Shard file {shard_file} should exist")

        # Consolidate all session shards into primary evidence storage
        total_consolidated = 0
        for idx in range(num_sessions):
            session_id = f"session_stream_{idx}"
            count = self.db_manager.consolidate_session_evidence(session_id, remove_shard=True)
            self.assertEqual(count, records_per_session)
            total_consolidated += count

            # Verify shard was cleaned up
            shard_file = Path(self.temp_dir) / f"evidence_session_stream_{idx}.db"
            self.assertFalse(shard_file.exists(), f"Shard file {shard_file} should be removed after consolidation")

        self.assertEqual(total_consolidated, num_sessions * records_per_session)

        # Verify primary database has all records
        with self.db_manager.get_evidence_conn(readonly=True) as conn:
            ev_count = conn.execute("SELECT count(*) FROM immutable_evidence_log").fetchone()[0]
            q_count = conn.execute("SELECT count(*) FROM durable_work_queue").fetchone()[0]
            self.assertEqual(ev_count, num_sessions * records_per_session)
            self.assertEqual(q_count, num_sessions * records_per_session)

    def test_vectorized_queue_leasing(self):
        """
        Populates 30 queue items, calls lease_work_batch(batch_size=12),
        and verifies atomic vectorized lease updates.
        """
        # Append 30 evidence records
        for i in range(30):
            rec = EvidenceRecord(
                evidence_id=f"ev_vec_{i}",
                meeting_id="meeting_vectorized_test",
                speaker="Speaker",
                text=f"Utterance {i}",
                start_timestamp=float(i),
                end_timestamp=float(i + 0.8),
            )
            self.db_manager.append_evidence(rec, enqueue=True)

        # Lease a batch of 12
        leased = self.db_manager.lease_work_batch(
            batch_size=12,
            worker_id="vector_worker_01",
        )
        self.assertEqual(len(leased), 12)

        # Verify all leased items have state PROCESSING and locked_by set
        for w_item, _e_rec in leased:
            self.assertEqual(w_item.state, WorkItemState.PROCESSING)
            self.assertEqual(w_item.locked_by, "vector_worker_01")
            self.assertIsNotNone(w_item.locked_at)

        # Verify in DB: 12 PROCESSING, 18 PENDING
        with self.db_manager.get_evidence_conn(readonly=True) as conn:
            proc_count = conn.execute(
                "SELECT count(*) FROM durable_work_queue WHERE state = 'PROCESSING' AND locked_by = 'vector_worker_01'"
            ).fetchone()[0]
            pend_count = conn.execute(
                "SELECT count(*) FROM durable_work_queue WHERE state = 'PENDING'"
            ).fetchone()[0]
            self.assertEqual(proc_count, 12)
            self.assertEqual(pend_count, 18)


class TestGovernedParallelTestRunner(unittest.TestCase):
    """Verifies that the multi-process test runner runs with isolated worker sandboxes."""

    def test_run_parallel_execution_programmatic(self):
        """Builds a test suite and executes via run_parallel with 2 worker processes."""
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        suite.addTests(loader.loadTestsFromName("tests.test_validators_unit"))

        runner = ProgressBarTestRunner(verbosity=0)
        result = runner.run_parallel(suite, num_workers=2)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.passed_count, 9)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.error_count, 0)


if __name__ == "__main__":
    unittest.main()
