"""
tests/test_hitl_sqlite_store.py

Unit and integration tests for Layer-0 HITLSQLiteStore, GovernedStorageContext concurrency,
and hitl_decision_ledger.json -> SQLite migration tooling.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance import HITLDecisionStore
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore
from tools.migrate_hitl_ledger_to_sqlite import migrate_json_to_sqlite


class TestHITLSQLiteStore(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.temp_dir / "test_hitl_ledger.sqlite"
        self.store = HITLSQLiteStore(self.db_path)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_creates_table_and_indexes(self) -> None:
        """Database initializes with WAL mode, tables, and indexes."""
        self.assertTrue(self.db_path.exists())
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(self.store.latest_entry_hash(), "0" * 64)

        with GovernedStorageContext.read_context(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='hitl_decisions';")
            self.assertIsNotNone(cur.fetchone())

            cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_hitl_category';")
            self.assertIsNotNone(cur.fetchone())

    def test_record_decision_merkle_chaining(self) -> None:
        """Sequential decisions form an immutable Merkle hash chain with monotonic sequence numbers."""
        rec1 = self.store.record_decision(
            decision_id="dec-001",
            category="architecture",
            prompt="Choose database engine",
            options=["sqlite", "postgres"],
            selected_option="sqlite",
            rationale="Zero latency, local hermeticity",
            operator_id="human_operator",
        )

        self.assertEqual(rec1["sequence_number"], 0)
        self.assertEqual(rec1["previous_entry_hash"], "0" * 64)
        self.assertEqual(len(rec1["entry_digest"]), 64)

        rec2 = self.store.record_decision(
            decision_id="dec-002",
            category="architecture",
            prompt="Choose schema format",
            options=["json", "relational"],
            selected_option="relational",
            rationale="Typed columns, B-tree indexes",
            operator_id="human_operator",
        )

        self.assertEqual(rec2["sequence_number"], 1)
        self.assertEqual(rec2["previous_entry_hash"], rec1["entry_digest"])
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.latest_entry_hash(), rec2["entry_digest"])

    def test_get_history_and_filtering(self) -> None:
        """History retrieval preserves ordering and supports category filtering."""
        self.store.record_decision(
            decision_id="dec-cat-1",
            category="speaker_attribution",
            prompt="Disambiguate speaker",
            options=["Alice", "Bob"],
            selected_option="Alice",
        )
        self.store.record_decision(
            decision_id="dec-cat-2",
            category="data_classification",
            prompt="Classify document",
            options=["internal", "confidential"],
            selected_option="confidential",
        )

        all_history = self.store.get_history()
        self.assertEqual(len(all_history), 2)
        self.assertEqual(all_history[0]["decision_id"], "dec-cat-1")
        self.assertEqual(all_history[1]["decision_id"], "dec-cat-2")

        speaker_history = self.store.get_history(category="speaker_attribution")
        self.assertEqual(len(speaker_history), 1)
        self.assertEqual(speaker_history[0]["decision_id"], "dec-cat-1")

        data_history = self.store.get_history(category="data_classification")
        self.assertEqual(len(data_history), 1)
        self.assertEqual(data_history[0]["decision_id"], "dec-cat-2")

    def test_get_decision_by_id(self) -> None:
        """Decisions can be fetched directly by decision_id or entry_id."""
        rec = self.store.record_decision(
            decision_id="target-dec-123",
            category="review",
            prompt="Approve PR",
            options=["yes", "no"],
            selected_option="yes",
        )

        by_dec_id = self.store.get_decision("target-dec-123")
        self.assertIsNotNone(by_dec_id)
        self.assertEqual(by_dec_id["decision_id"], "target-dec-123")

        by_entry_id = self.store.get_decision(rec["entry_id"])
        self.assertIsNotNone(by_entry_id)
        self.assertEqual(by_entry_id["entry_id"], rec["entry_id"])

        nonexistent = self.store.get_decision("ghost-id")
        self.assertIsNone(nonexistent)


class TestHITLDecisionStoreIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp())
        self.sqlite_path = self.temp_dir / "integrated_ledger.sqlite"
        self.json_path = self.temp_dir / "integrated_ledger.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_hitl_decision_store_sqlite_mode(self) -> None:
        """HITLDecisionStore delegates to SQLite when configured with sqlite path."""
        store = HITLDecisionStore(sqlite_path=self.sqlite_path)
        self.assertTrue(store.use_sqlite)

        rec = store.record_decision(
            decision_id="int-001",
            category="routing",
            prompt="Select execution path",
            options=["fast", "standard"],
            selected_option="fast",
            operator_id="operator_1",
        )

        self.assertEqual(rec["decision_id"], "int-001")
        history = store.get_history("routing")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["selected_key"], "fast")

        # Test calibrate_options with Bayesian adjustment
        options = [
            {"label": "fast", "confidence": 0.50},
            {"label": "standard", "confidence": 0.50},
        ]
        calibrated = store.calibrate_options(options, category="routing")
        self.assertEqual(len(calibrated), 2)
        fast_opt = next(o for o in calibrated if o.get("key") == "fast" or o.get("label") == "fast")
        self.assertGreaterEqual(fast_opt["confidence"], 0.50)

    def test_migration_from_json_to_sqlite(self) -> None:
        """Existing JSON ledger records migrate cleanly and idempotently to SQLite."""
        # Create a mock JSON ledger
        mock_data = {
            "schema_version": "1.0.0",
            "record_count": 2,
            "decisions": [
                {
                    "entry_id": "mig-001",
                    "sequence_number": 0,
                    "decision_id": "legacy-dec-1",
                    "category": "legacy_cat",
                    "operator_id": "human_operator",
                    "prompt": "Legacy prompt 1",
                    "options": ["A", "B"],
                    "selected_option": "A",
                    "selected_key": "A",
                    "rationale": "Historical choice",
                    "previous_entry_hash": "0" * 64,
                    "recorded_at": "2026-09-15T12:00:00Z",
                    "metadata": {"test": True},
                    "entry_digest": "abcdef1234567890" * 4,
                },
                {
                    "entry_id": "mig-002",
                    "sequence_number": 1,
                    "decision_id": "legacy-dec-2",
                    "category": "legacy_cat",
                    "operator_id": "human_operator",
                    "prompt": "Legacy prompt 2",
                    "options": ["C", "D"],
                    "selected_option": "C",
                    "selected_key": "C",
                    "rationale": "Historical choice 2",
                    "previous_entry_hash": "abcdef1234567890" * 4,
                    "recorded_at": "2026-09-15T12:05:00Z",
                    "metadata": {"test": True},
                    "entry_digest": "1234567890abcdef" * 4,
                },
            ],
        }
        self.json_path.write_text(json.dumps(mock_data), encoding="utf-8")

        result = migrate_json_to_sqlite(self.json_path, self.sqlite_path)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["migrated_count"], 2)

        store = HITLSQLiteStore(self.sqlite_path)
        self.assertEqual(store.count(), 2)

        mig1 = store.get_decision("legacy-dec-1")
        self.assertIsNotNone(mig1)
        self.assertEqual(mig1["selected_key"], "A")
        self.assertEqual(mig1["metadata"], {"test": True})

        # Idempotent re-run
        result2 = migrate_json_to_sqlite(self.json_path, self.sqlite_path)
        self.assertEqual(result2["status"], "COMPLETE")
        self.assertEqual(store.count(), 2)


if __name__ == "__main__":
    unittest.main()
