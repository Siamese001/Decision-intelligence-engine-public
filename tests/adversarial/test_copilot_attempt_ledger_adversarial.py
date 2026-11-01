"""
tests/adversarial/test_copilot_attempt_ledger_adversarial.py

Adversarial and edge-case verification for Copilot attempt records and exception persistence.
Satisfies e2e-and-edge-case-coverage.md requirements by covering >= 3 mandatory categories:
- Category 1: Boundary payload (None fields, empty inputs, null candidates).
- Category 2: Malformed input (corrupted existing JSONL lines, unwriteable paths).
- Category 3: Concurrency & race (multi-threaded concurrent append to same attempts file).
- Category 4: Rollback & recovery (fail-safe persistence on I/O error without crashing).
- Category 5: Invariant bypass attempt (prohibition of failed attempts in copilot_answers.jsonl).
"""
from __future__ import annotations

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli.copilot_attempt_ledger import (
    CopilotAttemptRecord,
    create_attempt_record,
    extract_context_freshness,
    record_copilot_attempt,
)


class TestCopilotAttemptLedgerAdversarial(unittest.TestCase):
    """Adversarial stress and edge-case tests for CopilotAttemptLedger."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._temp_dir.name)

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    # Category 1: Boundary Payload
    def test_adv_boundary_payload_null_and_empty_fields(self) -> None:
        """Verifies handling of boundary payloads with None, empty strings, and empty structures."""
        rec = create_attempt_record(
            terminal_outcome="FAIL",
            terminal_reason="",
            details="",
            request="",
            parsed_intent=None,
            parsed_target=None,
            parsed_topic=None,
            session_id=None,
            resolved_roots={},
            context_model=None,
            candidate_counts={},
            rejection_categories={},
        )

        self.assertEqual(rec.terminal_outcome, "FAIL")
        self.assertIsNotNone(rec.attempt_id)
        self.assertIsNotNone(rec.timestamp)
        self.assertEqual(rec.context_freshness["turn_count"], 0)
        self.assertEqual(rec.candidate_counts, {})

        written = record_copilot_attempt(rec, output_dir=self.tmp_path)
        self.assertIsNotNone(written)
        self.assertTrue(written.is_file())

        with open(written, "r", encoding="utf-8") as f:
            data = json.loads(f.read().strip())
        self.assertEqual(data["attempt_id"], rec.attempt_id)

    # Category 2: Malformed Input
    def test_adv_malformed_existing_jsonl_append_preservation(self) -> None:
        """Verifies appending to an attempts file that already contains malformed/truncated lines."""
        attempts_file = self.tmp_path / "copilot_attempts.jsonl"
        # Pre-populate with corrupted lines
        with open(attempts_file, "w", encoding="utf-8") as f:
            f.write('{"attempt_id": "corrupted-1", "incomplete": true\n')  # truncated
            f.write('NOT_A_JSON_STRING\n')
            f.write('\n')

        rec = create_attempt_record(
            terminal_outcome="FAIL",
            terminal_reason="NO_SESSION_FOUND",
            details="Recovery test after corrupted entries",
        )

        written = record_copilot_attempt(rec, output_dir=self.tmp_path)
        self.assertEqual(written, attempts_file)

        # Read back and ensure the valid record is preserved and cleanly parseable
        with open(attempts_file, "r", encoding="utf-8") as f:
            raw_lines = [line.strip() for line in f if line.strip()]

        self.assertEqual(len(raw_lines), 3)
        valid_record = json.loads(raw_lines[-1])
        self.assertEqual(valid_record["attempt_id"], rec.attempt_id)
        self.assertEqual(valid_record["terminal_reason"], "NO_SESSION_FOUND")

    # Category 3: Concurrency & Race
    def test_adv_concurrency_race_parallel_attempt_logging(self) -> None:
        """Verifies 20 concurrent threads writing attempt records simultaneously without file corruption."""
        worker_count = 20
        records = [
            create_attempt_record(
                terminal_outcome="FAIL",
                terminal_reason=f"REASON_{i}",
                details=f"Thread worker {i} load",
                session_id=f"session-{i}",
            )
            for i in range(worker_count)
        ]

        def _worker(rec: CopilotAttemptRecord) -> Path:
            res = record_copilot_attempt(rec, output_dir=self.tmp_path)
            assert res is not None
            return res

        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_worker, r) for r in records]
            done, not_done = concurrent.futures.wait(futures, timeout=10.0)

        self.assertEqual(len(not_done), 0)
        self.assertEqual(len(done), worker_count)

        attempts_file = self.tmp_path / "copilot_attempts.jsonl"
        self.assertTrue(attempts_file.exists())

        with open(attempts_file, "r", encoding="utf-8") as f:
            parsed_entries = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(parsed_entries), worker_count)
        logged_reasons = {e["terminal_reason"] for e in parsed_entries}
        expected_reasons = {f"REASON_{i}" for i in range(worker_count)}
        self.assertEqual(logged_reasons, expected_reasons)

    # Category 4: Rollback & Recovery
    def test_adv_recovery_failsafe_unwriteable_storage(self) -> None:
        """Verifies that unwriteable file permissions trigger fail-safe abstention without raising or blocking."""
        rec = create_attempt_record(
            terminal_outcome="FAIL",
            terminal_reason="NO_LIVE_CONTEXT",
        )

        with patch("builtins.open", side_effect=PermissionError("Permission denied [Errno 13]")):
            res = record_copilot_attempt(rec, output_dir=self.tmp_path)
            # Must fail safe and return None, never raise PermissionError
            self.assertIsNone(res)

    # Category 5: Invariant Bypass Attempt
    def test_adv_invariant_bypass_failure_never_leaks_to_answers_ledger(self) -> None:
        """Verifies invariant: failed attempts are barred from copilot_answers.jsonl."""
        out_dir = self.tmp_path / "protected_meeting"
        out_dir.mkdir(parents=True, exist_ok=True)

        rec = create_attempt_record(
            terminal_outcome="FAIL",
            terminal_reason="ZERO_RELEVANT_ASSERTIONS",
            details="Unverified assertions",
        )

        record_copilot_attempt(rec, output_dir=out_dir)

        # Invariant check: copilot_answers.jsonl MUST NOT exist or contain any failed attempts
        answers_file = out_dir / "copilot_answers.jsonl"
        self.assertFalse(answers_file.exists(), "Invariant violation: copilot_answers.jsonl was created for a failure")


if __name__ == "__main__":
    unittest.main()
