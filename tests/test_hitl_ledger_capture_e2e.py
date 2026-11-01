"""
tests/test_hitl_ledger_capture_e2e.py

End-to-end test suite for canonical ledger path resolution, ask_user_question capture hook,
and H3 learning_event telemetry emission under concurrent writes (Wave 5).
"""
import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance import (
    HITLDecisionStore,
    resolve_hitl_ledger_path,
)


class HitlLedgerCaptureE2ETests(unittest.TestCase):
    """E2E verification of ledger resolution, capture, and H3 telemetry."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()
        self.ledger_file = (self.root / "hitl_decision_ledger.json").resolve()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_canonical_ledger_path_resolution(self) -> None:
        """Verify resolve_hitl_ledger_path resolves to canonical root path."""
        resolved = resolve_hitl_ledger_path(self.root)
        self.assertEqual(resolved, self.ledger_file)
        self.assertEqual(resolved.name, "hitl_decision_ledger.json")

    def test_record_decision_emits_h3_learning_event_and_maintains_merkle_chain(self) -> None:
        """Verify record_decision writes Merkle linked entry and emits H3 learning_event."""
        store = HITLDecisionStore(ledger_path=self.ledger_file)
        rec1 = store.record_decision(
            decision_id="dec-1",
            category="routing",
            prompt="Route to production?",
            options=[
                {"label": "Option A", "confidence_score": 0.90},
                {"label": "Option B", "confidence_score": 0.60},
            ],
            selected_option={"label": "Option A", "confidence_score": 0.90},
            operator_id="operator_1",
        )
        self.assertEqual(rec1["outcome_type"], "AFFIRMED")
        self.assertEqual(rec1["previous_entry_hash"], "0" * 64)

        rec2 = store.record_decision(
            decision_id="dec-2",
            category="routing",
            prompt="Quarantine candidate?",
            options=[
                {"label": "Option A", "confidence_score": 0.85},
                {"label": "Option B", "confidence_score": 0.65},
            ],
            selected_option={"label": "Option B", "confidence_score": 0.65},
            operator_id="operator_1",
        )
        self.assertEqual(rec2["outcome_type"], "OVERRIDDEN")
        self.assertEqual(rec2["previous_entry_hash"], rec1["entry_digest"])

        # Verify H3 telemetry emission in SQLite
        db_path = self.ledger_file.parent / "telemetry_ledger.sqlite"
        self.assertTrue(db_path.exists(), "telemetry_ledger.sqlite must exist")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT stage, event_type, status, payload_digest FROM learning_event ORDER BY created_at ASC")
        rows = cursor.fetchall()
        conn.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "H3")
        self.assertEqual(rows[0][1], "hitl_decision_resolved")
        self.assertEqual(rows[0][2], "OK")
        self.assertEqual(rows[0][3], rec1["entry_digest"])

        self.assertEqual(rows[1][0], "H3")
        self.assertEqual(rows[1][2], "OVERRIDDEN")
        self.assertEqual(rows[1][3], rec2["entry_digest"])

    def test_post_tool_call_hook_configured_for_ask_user_question(self) -> None:
        """Verify .agents/hooks.json contains nee-hitl-ledger-capture matching ask_user_question."""
        hooks_path = Path(".agents/hooks.json")
        self.assertTrue(hooks_path.exists())
        data = json.loads(hooks_path.read_text())
        hooks = data.get("hooks", [])
        capture_hooks = [h for h in hooks if h.get("id") == "nee-hitl-ledger-capture"]
        self.assertEqual(len(capture_hooks), 1)
        self.assertEqual(capture_hooks[0]["event"], "post_tool_call")
        self.assertEqual(capture_hooks[0]["match"]["tool"], "ask_user_question")

    def test_concurrent_writers_preserve_merkle_chain_integrity(self) -> None:
        """Verify 8 concurrent workers recording decisions maintain Merkle chain without corruption."""
        store = HITLDecisionStore(ledger_path=self.ledger_file)
        num_workers = 8

        def worker(worker_id: int):
            return store.record_decision(
                decision_id=f"concurrent-{worker_id}",
                category="concurrency_test",
                prompt=f"Worker {worker_id} prompt",
                options=[
                    {"label": "Approve", "confidence_score": 0.95},
                    {"label": "Reject", "confidence_score": 0.10},
                ],
                selected_option={"label": "Approve", "confidence_score": 0.95},
                operator_id=f"worker_{worker_id}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
            futures = [executor.submit(worker, i) for i in range(num_workers)]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), num_workers)
        # Load records with integrity check
        verified_records = store._load_records(verify_integrity=True)
        self.assertEqual(len(verified_records), num_workers)
        # Verify sequence and Merkle linking
        for i in range(1, len(verified_records)):
            self.assertEqual(
                verified_records[i]["previous_entry_hash"],
                verified_records[i - 1]["entry_digest"],
                f"Broken Merkle link at index {i}",
            )


if __name__ == "__main__":
    unittest.main()
