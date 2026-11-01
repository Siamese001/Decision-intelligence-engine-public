"""tests/test_graphdb_sourcing_wave_receipts.py

Test suite verifying the cryptographic wave receipt coordinator and predecessor gating.
Asserts that:
1. Wave 1 produces an authentic execution receipt with valid cryptographic digest.
2. Wave N (N > 1) rejects missing or failed predecessor receipts fail-closed.
3. Tampered receipts, altered payloads, and fabricated digests are detected and rejected.
4. Failing, errored, or skipped tests fail closed (zero skipped required tests invariant).
5. Graph mutations between before and after trigger immediate verification failure.
6. Reintroduction of prohibited offline flags is blocked.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.run_graphdb_sourcing_wave import (
    compute_dict_digest,
    compute_file_sha256,
    run_wave,
    verify_wave_receipt,
    write_atomic_json,
)


class TestGraphdbSourcingWaveReceipts(unittest.TestCase):
    """Qualification suite for wave receipt generation and integrity gates."""

    def setUp(self) -> None:
        self.temp_dir_obj = tempfile.TemporaryDirectory()
        self.temp_dir = Path(self.temp_dir_obj.name)

    def tearDown(self) -> None:
        self.temp_dir_obj.cleanup()

    @patch("tools.run_graphdb_sourcing_wave.unittest.TextTestRunner")
    def test_wave1_receipt_generation_and_sealing(self, mock_runner_cls: MagicMock) -> None:
        """Verify Wave 1 runs tests, bootstraps predecessor, and seals valid cryptographic receipt."""
        mock_result = MagicMock()
        mock_result.wasSuccessful.return_value = True
        mock_result.testsRun = 12
        mock_result.failures = []
        mock_result.errors = []
        mock_result.skipped = []
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_result
        mock_runner_cls.return_value = mock_runner

        dummy_graph = self.temp_dir / "graph.sqlite"
        dummy_graph.write_text("dummy-graph-data-v1")

        exit_code, receipt = run_wave(
            wave=1,
            receipts_dir=self.temp_dir,
            graph_path=dummy_graph,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(receipt["terminal_outcome"], "PASS")
        self.assertEqual(receipt["wave_status"], "COMPLETE")
        self.assertIsNone(receipt["predecessor_receipt"])
        self.assertTrue(receipt["graph_unchanged"])
        self.assertTrue(receipt["production_controls_preserved"])

        receipt_file = self.temp_dir / "graphdb_sourcing_wave_1_receipt.json"
        self.assertTrue(receipt_file.is_file())

        verified = verify_wave_receipt(receipt_file)
        self.assertEqual(verified["receipt_digest"], receipt["receipt_digest"])

    def test_wave2_rejects_missing_predecessor(self) -> None:
        """Verify Wave 2 without an existing Wave 1 receipt fails closed immediately."""
        exit_code, receipt = run_wave(
            wave=2,
            receipts_dir=self.temp_dir,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertEqual(receipt["wave_status"], "BLOCKED")
        self.assertIn("PREDECESSOR_VERIFICATION_FAILED", receipt["error"])

    def test_wave2_rejects_failed_or_blocked_predecessor(self) -> None:
        """Verify Wave 2 rejects a predecessor receipt whose status is not COMPLETE/PASS."""
        bad_w1 = {
            "schema_version": "1.0.0",
            "wave": 1,
            "wave_title": "Wave 1",
            "terminal_outcome": "FAILED",
            "wave_status": "BLOCKED",
            "timestamp": "2026-10-06T12:00:00Z",
        }
        bad_w1["receipt_digest"] = compute_dict_digest(bad_w1)
        write_atomic_json(self.temp_dir / "graphdb_sourcing_wave_1_receipt.json", bad_w1)

        exit_code, receipt = run_wave(
            wave=2,
            receipts_dir=self.temp_dir,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertEqual(receipt["wave_status"], "BLOCKED")
        self.assertIn("not COMPLETE/PASS", receipt["error"])

    def test_verify_receipt_detects_tampered_payload(self) -> None:
        """Verify that altering any field in a sealed receipt invalidates its digest."""
        receipt_body = {
            "schema_version": "1.0.0",
            "wave": 1,
            "wave_status": "COMPLETE",
            "terminal_outcome": "PASS",
            "timestamp": "2026-10-06T12:00:00Z",
        }
        digest = compute_dict_digest(receipt_body)
        receipt_body["receipt_digest"] = digest

        receipt_file = self.temp_dir / "test_receipt.json"
        write_atomic_json(receipt_file, receipt_body)

        # 1. Unmodified receipt verifies cleanly
        verified = verify_wave_receipt(receipt_file)
        self.assertEqual(verified["receipt_digest"], digest)

        # 2. Tamper with a field without recomputing digest
        tampered_body = dict(receipt_body)
        tampered_body["wave_status"] = "FABRICATED"
        write_atomic_json(receipt_file, tampered_body)

        with self.assertRaises(ValueError) as ctx:
            verify_wave_receipt(receipt_file)
        self.assertIn("Receipt digest mismatch", str(ctx.exception))

    def test_verify_receipt_missing_file_or_missing_digest_raises(self) -> None:
        """Verify missing receipt file raises FileNotFoundError, missing digest raises ValueError."""
        nonexistent = self.temp_dir / "does_not_exist.json"
        with self.assertRaises(FileNotFoundError):
            verify_wave_receipt(nonexistent)

        no_digest_file = self.temp_dir / "no_digest.json"
        write_atomic_json(no_digest_file, {"schema_version": "1.0.0"})
        with self.assertRaises(ValueError) as ctx:
            verify_wave_receipt(no_digest_file)
        self.assertIn("missing receipt_digest", str(ctx.exception))

    @patch("tools.run_graphdb_sourcing_wave.unittest.TextTestRunner")
    def test_wave_runner_fails_on_skipped_tests(self, mock_runner_cls: MagicMock) -> None:
        """Verify that even 1 skipped required test causes fail-closed wave status BLOCKED."""
        mock_result = MagicMock()
        mock_result.wasSuccessful.return_value = True
        mock_result.testsRun = 10
        mock_result.failures = []
        mock_result.errors = []
        mock_result.skipped = [("TestClass.test_name", "Skipped reason")]
        mock_runner = MagicMock()
        mock_runner.run.return_value = mock_result
        mock_runner_cls.return_value = mock_runner

        exit_code, receipt = run_wave(
            wave=1,
            receipts_dir=self.temp_dir,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertEqual(receipt["wave_status"], "BLOCKED")
        self.assertEqual(receipt["test_counts"]["skipped"], 1)

    @patch("tools.run_graphdb_sourcing_wave.unittest.TextTestRunner")
    def test_wave_runner_fails_on_graph_mutation(self, mock_runner_cls: MagicMock) -> None:
        """Verify that graph modification during test execution causes fail-closed outcome."""
        dummy_graph = self.temp_dir / "graph.sqlite"
        dummy_graph.write_text("version-1")

        def side_effect_mutate(suite):
            # Mutate graph during run
            dummy_graph.write_text("version-2-mutated")
            res = MagicMock()
            res.wasSuccessful.return_value = True
            res.testsRun = 5
            res.failures = []
            res.errors = []
            res.skipped = []
            return res

        mock_runner = MagicMock()
        mock_runner.run.side_effect = side_effect_mutate
        mock_runner_cls.return_value = mock_runner

        exit_code, receipt = run_wave(
            wave=1,
            receipts_dir=self.temp_dir,
            graph_path=dummy_graph,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertEqual(receipt["wave_status"], "BLOCKED")
        self.assertFalse(receipt["graph_unchanged"])
        self.assertEqual(receipt["graph_mutations"], 1)

    @patch("tools.run_graphdb_sourcing_wave.validate_production_controls_preserved")
    def test_wave_runner_fails_if_prohibited_flags_reintroduced(self, mock_validate: MagicMock) -> None:
        """Verify that if prohibited offline flags are detected, execution halts immediately."""
        mock_validate.return_value = False

        exit_code, receipt = run_wave(
            wave=1,
            receipts_dir=self.temp_dir,
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["terminal_outcome"], "FAILED")
        self.assertEqual(receipt["wave_status"], "BLOCKED")
        self.assertIn("PRODUCTION_CONTROLS_VIOLATED", receipt["error"])


if __name__ == "__main__":
    unittest.main()
