"""End-to-End lifecycle tests for AI-DLC KPI Report and Gate Run Ledger."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.aidlc_kpi_report import (
    compare_reports,
    compute_kpi_metrics,
)
from tools.gate_run_ledger import (
    read_gate_runs,
    record_gate_run,
    summarize_gate_runs,
    track_gate_execution,
)


class TestAidlcKpiReportE2E(unittest.TestCase):
    """Exercises full end-to-end lifecycle for AI-DLC KPI reporting."""

    def test_compute_kpi_metrics_on_head_range(self) -> None:
        """Verifies full report schema on real git repository commits."""
        report = compute_kpi_metrics(commit_range="HEAD~5..HEAD", count=5)
        self.assertIn("report_id", report)
        self.assertIn("timestamp_utc", report)
        self.assertIn("input_commits_digest", report)
        self.assertEqual(len(report["input_commits_digest"]), 64)
        self.assertIn("total_commits", report)
        self.assertGreaterEqual(report["total_commits"], 1)
        self.assertIn("merges_count", report)
        self.assertIn("non_merges_count", report)
        self.assertIn("commit_mix", report)
        self.assertIn("rework", report)
        self.assertIn("seal", report)
        self.assertIn("hotfile", report)
        self.assertIn("batch_size", report)
        self.assertIn("lead_time", report)
        self.assertIn("origin_lag_commits", report)

    def test_report_comparison_lifecycle(self) -> None:
        """Verifies diff calculation between baseline and current report."""
        base = {
            "commit_range": "revA..revB",
            "rework": {"appeasement_commits_count": 15},
            "seal": {"seal_only_commits_count": 8},
            "hotfile": {"touches_count": 25},
            "lead_time": {"median_minutes": 40.0},
            "origin_lag_commits": 70,
        }
        curr = {
            "commit_range": "revB..revC",
            "rework": {"appeasement_commits_count": 3},
            "seal": {"seal_only_commits_count": 0},
            "hotfile": {"touches_count": 2},
            "lead_time": {"median_minutes": 15.0},
            "origin_lag_commits": 5,
        }
        diff = compare_reports(curr, base)
        self.assertIn("deltas", diff)
        self.assertEqual(diff["deltas"]["rework_commits"]["delta"], -12)
        self.assertEqual(diff["deltas"]["seal_commits"]["delta"], -8)
        self.assertEqual(diff["deltas"]["hotfile_touches"]["delta"], -23)
        self.assertEqual(diff["deltas"]["lead_time_p50_minutes"]["delta"], -25.0)
        self.assertEqual(diff["deltas"]["origin_lag"]["delta"], -65)

    def test_gate_run_ledger_roundtrip(self) -> None:
        """Verifies recording, reading, context management, and statistical summaries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_file = Path(tmpdir) / "test_ledger.jsonl"

            # 1. Record manual run
            event = record_gate_run(
                gate_name="test_lint",
                tier="pre_commit",
                wall_clock_seconds=0.15,
                status="PASS",
                cache_hit=False,
                metadata={"file_count": 10},
                ledger_path=ledger_file,
            )
            self.assertEqual(event["gate_name"], "test_lint")

            # 2. Context manager tracking PASS
            with track_gate_execution(
                "test_suite", tier="readiness", ledger_path=ledger_file
            ) as set_status:
                set_status("PASS")

            # 3. Context manager tracking FAIL
            with (
                self.assertRaises(ValueError),
                track_gate_execution("test_failing", tier="readiness", ledger_path=ledger_file),
            ):
                raise ValueError("Simulated gate error")

            # 4. Read back entries
            runs = read_gate_runs(ledger_path=ledger_file)
            self.assertEqual(len(runs), 3)
            self.assertEqual(runs[0]["status"], "PASS")
            self.assertEqual(runs[1]["status"], "PASS")
            self.assertEqual(runs[2]["status"], "FAIL")

            # 5. Summarize
            summary = summarize_gate_runs(ledger_path=ledger_file)
            self.assertEqual(summary["total_runs"], 3)
            self.assertEqual(summary["passes"], 2)
            self.assertEqual(summary["fails"], 1)
            self.assertIn("pre_commit", summary["by_tier"])
            self.assertIn("readiness", summary["by_tier"])


if __name__ == "__main__":
    unittest.main()
