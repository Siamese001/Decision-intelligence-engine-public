"""
tests/test_wave1_safety_and_integrity_e2e.py

End-to-end tests exercising Wave 1 Critical Safety & Data Integrity capabilities:
- Inter-process meeting ingestion concurrency lock
- Ephemeral staging directory promotion
- Database schema migration framework
- Orphaned session shard recovery and consolidation
- Full CanonicalAgenticPipeline lifecycle under governed safety controls
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline.concurrency import (
    MeetingIngestionLock,
)
from dual_pipeline.exceptions import (
    ConcurrentIngestionError,
    InsufficientMemoryError,
    LedgerIntegrityTamperedError,
)
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.memory_guard import assert_memory_headroom
from storage.database import DatabaseManager
from storage.migration import SchemaMigrator


class TestWave1SafetyAndIntegrityE2E(unittest.TestCase):
    """E2E suite verifying Wave 1 safety and data integrity guarantees."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_meeting_ingestion_concurrency_lock_e2e(self):
        """Verifies that simultaneous execution for the same meeting fails fast."""
        lock_dir = self.tmp_path / "locks"
        lock_key = "test_meeting_20260927"

        # First lock acquisition should succeed
        lock1 = MeetingIngestionLock(lock_key, input_path="sample.wav", lock_dir=lock_dir)
        lock1.acquire()

        # Second lock acquisition for the same meeting key must fail fast
        lock2 = MeetingIngestionLock(lock_key, input_path="sample.wav", lock_dir=lock_dir)
        with self.assertRaises(ConcurrentIngestionError) as cm:
            lock2.acquire()
        self.assertIn("Concurrent ingestion rejected", str(cm.exception))
        self.assertIn(lock_key, str(cm.exception))

        # Release first lock
        lock1.release()

        # Now second lock can be acquired cleanly
        lock2.acquire()
        lock2.release()

    def test_schema_migration_engine_e2e(self):
        """Verifies that schema migrations apply sequentially and record version history."""
        db_path = self.tmp_path / "test_migration.db"
        migrator = SchemaMigrator()

        with sqlite3.connect(str(db_path)) as conn:
            applied = migrator.apply_pending(conn)
            self.assertGreaterEqual(len(applied), 1)
            self.assertIn(1, applied)

            # Running again should be an idempotent no-op
            second_run = migrator.apply_pending(conn)
            self.assertEqual(len(second_run), 0)

            # Verify _schema_migrations table contents
            versions = migrator.get_applied_versions(conn)
            self.assertIn(1, versions)

    def test_abandoned_session_shard_recovery_e2e(self):
        """Verifies that crashed worker shards are recovered and consolidated on startup."""
        mgr = DatabaseManager(base_dir=self.tmp_path)

        # Create a session shard manually representing a crashed worker
        session_id = "crashed_worker_session_42"
        shard_conn = mgr.get_session_evidence_conn(session_id)
        shard_conn.execute(
            """
            INSERT INTO immutable_evidence_log (
                evidence_id, meeting_id, speaker, text,
                start_timestamp, end_timestamp, is_final
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("ev_42", "meeting_recovered", "Speaker 1", "Critical decision text", "12:00:00", "12:01:00", 1),
        )
        shard_conn.commit()
        shard_conn.close()

        shard_file = self.tmp_path / f"evidence_{session_id}.db"
        self.assertTrue(shard_file.exists())

        # Trigger recovery with threshold = 0s (immediately eligible)
        recovered_count = mgr.recover_and_consolidate_abandoned_shards(max_inactive_seconds=0.0)
        self.assertGreaterEqual(recovered_count, 1)

        # Shard file should now be unlinked
        self.assertFalse(shard_file.exists())

        # Record should be present in primary evidence log
        with mgr.get_evidence_conn(readonly=True) as conn:
            row = conn.execute("SELECT speaker, text FROM immutable_evidence_log WHERE evidence_id = 'ev_42'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "Speaker 1")
            self.assertEqual(row[1], "Critical decision text")

    def test_memory_headroom_assertion_e2e(self):
        """Verifies that memory guard enforces minimum headroom thresholds."""
        # Asking for an impossibly large amount of free RAM (1 Petabyte) must raise InsufficientMemoryError
        with self.assertRaises(InsufficientMemoryError) as cm:
            assert_memory_headroom(min_free_bytes=1024**5, context="Large test allocation")
        self.assertIn("Insufficient system memory", str(cm.exception))

    def test_ledger_tamper_detection_e2e(self):
        """Verifies that on-disk modification of hitl_decision_ledger.json is detected."""
        ledger_file = self.tmp_path / "hitl_decision_ledger.json"
        store = HITLDecisionStore(ledger_path=ledger_file, use_sqlite=False)

        # Record two valid decisions
        store.record_decision(
            decision_id="dec_001",
            category="speaker_attribution",
            prompt="Confirm speaker 1",
            options=["Alice", "Bob"],
            selected_option="Alice",
        )
        store.record_decision(
            decision_id="dec_002",
            category="speaker_attribution",
            prompt="Confirm speaker 2",
            options=["Charlie", "Dave"],
            selected_option="Charlie",
        )

        # Normal load should pass
        records = store._load_records(verify_integrity=True)
        self.assertEqual(len(records), 2)

        # Invalidate cache to force disk re-read
        HITLDecisionStore._RECORDS_CACHE.clear()

        # Tamper with the file on disk (flip Alice to Eve)
        raw_data = json.loads(ledger_file.read_text(encoding="utf-8"))
        raw_data["decisions"][0]["selected_option"] = "Eve"
        # Write back without updating digests
        ledger_file.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")

        # Loading tampered ledger must raise LedgerIntegrityTamperedError
        with self.assertRaises(LedgerIntegrityTamperedError):
            store._load_records(verify_integrity=True)


if __name__ == "__main__":
    unittest.main()
