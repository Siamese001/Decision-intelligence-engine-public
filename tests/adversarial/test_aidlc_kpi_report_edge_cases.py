"""Adversarial and boundary edge-case tests for AI-DLC KPI Report and Ledger."""

from __future__ import annotations

import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from tools.aidlc_kpi_report import (
    compute_kpi_metrics,
)
from tools.gate_run_ledger import (
    read_gate_runs,
    record_gate_run,
    track_gate_execution,
)


class TestAidlcKpiReportEdgeCases(unittest.TestCase):
    """Adversarial suite covering boundary inputs, concurrency races, and error safety."""

    def test_invalid_commit_range_handled_gracefully(self) -> None:
        """Verifies invalid or non-existent commit range returns error payload rather than crashing."""
        res = compute_kpi_metrics(commit_range="non_existent_sha_0000..non_existent_sha_1111")
        self.assertEqual(res.get("total_commits"), 0)
        self.assertIn("error", res)

    def test_empty_commits_list_division_by_zero_prevention(self) -> None:
        """Verifies zero commits or zero merges does not cause ZeroDivisionError."""
        # compute on an empty synthetic range
        res = compute_kpi_metrics(commit_range="HEAD..HEAD")
        self.assertIn("total_commits", res)
        self.assertEqual(res["total_commits"], 0)

    def test_concurrent_ledger_writes_no_corruption(self) -> None:
        """Verifies multithreaded concurrent writes to the JSONL ledger do not corrupt lines."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = Path(tmpdir) / "concurrent_ledger.jsonl"
            num_threads = 8
            writes_per_thread = 25
            total_expected = num_threads * writes_per_thread

            def _writer_task(thread_id: int) -> None:
                for i in range(writes_per_thread):
                    record_gate_run(
                        gate_name=f"gate_t{thread_id}_{i}",
                        tier="concurrency_test",
                        wall_clock_seconds=0.01 * (i + 1),
                        status="PASS" if i % 2 == 0 else "FAIL",
                        ledger_path=ledger_file,
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = [executor.submit(_writer_task, tid) for tid in range(num_threads)]
                for f in concurrent.futures.as_completed(futures):
                    f.result()

            runs = read_gate_runs(ledger_path=ledger_file)
            self.assertEqual(len(runs), total_expected)
            # Verify every entry is a valid deserialized JSON object with required keys
            for r in runs:
                self.assertIn("gate_name", r)
                self.assertIn("status", r)
                self.assertIn("wall_clock_seconds", r)

    def test_unwritable_ledger_path_no_op_safety(self) -> None:
        """Verifies that an uncreatable / unwritable path fails silently without crashing caller."""
        # Intentionally invalid path on posix (null byte in path or impossible root path)
        invalid_path = Path("/dev/null/impossible_dir/ledger.jsonl")
        try:
            # Should not raise any exception
            event = record_gate_run(
                gate_name="silent_safety_check",
                tier="safety",
                wall_clock_seconds=0.005,
                status="PASS",
                ledger_path=invalid_path,
            )
            self.assertEqual(event["status"], "PASS")
        except Exception as e:
            self.fail(f"record_gate_run raised unexpected exception on unwritable path: {e}")

    def test_context_manager_exception_propagation(self) -> None:
        """Verifies context manager always records FAIL and re-raises exception faithfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = Path(tmpdir) / "exc_ledger.jsonl"
            with (
                self.assertRaises(RuntimeError) as ctx,
                track_gate_execution("crashing_gate", tier="test", ledger_path=ledger_file),
            ):
                raise RuntimeError("Intentional adversarial fault")
            self.assertIn("Intentional adversarial fault", str(ctx.exception))

            runs = read_gate_runs(ledger_path=ledger_file)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["gate_name"], "crashing_gate")
            self.assertEqual(runs[0]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
