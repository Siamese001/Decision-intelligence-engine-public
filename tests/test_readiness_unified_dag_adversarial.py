#!/usr/bin/env python3
"""Adversarial and concurrency tests for Unified 32-Stage Longest-Job-First (LJF) DAG.

Validates:
1. Stage 28 starts at t=0.0s (queue_wait < 100ms).
2. All 32 stages are reported in index order with valid PASS/FAIL receipts.
3. Empty-diff and non-Python diffs fast-pass Gates 4, 15, and file-scoped linters in <0.5s.
4. Gate 31 isolation does not mutate or remove repository learning/ or .local/copilot.
5. Atomic TestTimingDB writes survive concurrent worker execution without file corruption.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from tools.readiness_concurrent import (
    STAGE_P90_PRIORITIES,
    _execute_unified_stages_ljf,
)
from tools.readiness_core import (
    FILE_SCOPED_PYTHON_GATES,
    ROOT,
    is_empty_scope_skippable,
)
from tools.test_timing_db import TestTimingDB, get_timing_db_path
from tools.verify_copilot_smoke import verify_copilot_active_graph_smoke


class TestReadinessUnifiedDAGAdversarial(unittest.TestCase):
    """Adversarial and concurrency test cases for Wave 2 unified DAG."""

    def test_01_stage_28_scheduled_first_and_starts_immediately(self) -> None:
        """Category: Concurrency & race - Stage 29 (Core Test Suite) starts at t=0.0s with queue wait < 100ms."""
        self.assertIn(29, STAGE_P90_PRIORITIES)
        self.assertEqual(max(STAGE_P90_PRIORITIES.values()), STAGE_P90_PRIORITIES[29])

        start_times = {}

        def mock_run_stage(name: str, cmd: list) -> tuple[bool, str, float]:
            stage_idx = int(cmd[0])
            start_times[stage_idx] = time.monotonic()
            if stage_idx == 29:
                time.sleep(0.05)
            else:
                time.sleep(0.01)
            return True, f"{name} ok", 0.01

        simulated_stages = [
            (idx, f"Stage {idx}", [str(idx), "arg"]) for idx in range(1, 35)
        ]

        t_launch = time.monotonic()
        results, passed = _execute_unified_stages_ljf(
            stages_slice=simulated_stages,
            total_stages=34,
            use_cache=False,
            max_workers=8,
            head_sha="0000000000000000000000000000000000000000",
            target_files=["file.py"],
            verbose=False,
            repo_root=ROOT,
            run_stage_fn=mock_run_stage,
        )

        self.assertTrue(passed)
        self.assertEqual(len(results), 34)

        # Find Stage 29 entry
        stage_29_entry = next(r for r in results if r["stage_index"] == 29)
        self.assertEqual(stage_29_entry["status"], "PASS")
        self.assertLess(
            stage_29_entry["queue_wait_seconds"],
            0.10,
            f"Stage 29 queue wait was {stage_29_entry['queue_wait_seconds']}s (expected < 0.10s)",
        )

        # Verify Stage 29 started before lighter stages finished
        stage_29_start = start_times[29]
        self.assertLess(stage_29_start - t_launch, 0.05)

    def test_02_all_32_stages_emit_valid_schema_in_index_order(self) -> None:
        """Category: Boundary payload - All 32 stages emit complete valid receipt records in index order."""
        simulated_stages = [
            (idx, f"Gate {idx}", ["python", "-c", "pass"]) for idx in range(1, 33)
        ]

        results, passed = _execute_unified_stages_ljf(
            stages_slice=simulated_stages,
            total_stages=32,
            use_cache=False,
            max_workers=4,
            head_sha="0000000000000000000000000000000000000000",
            target_files=["foo.py"],
            verbose=False,
            repo_root=ROOT,
            run_stage_fn=lambda name, cmd: (True, "output", 0.005),
        )

        self.assertTrue(passed)
        self.assertEqual(len(results), 32)

        for i, entry in enumerate(results, start=1):
            self.assertEqual(entry["stage_index"], i)
            self.assertEqual(entry["status"], "PASS")
            self.assertIn("duration_seconds", entry)
            self.assertIn("queue_wait_seconds", entry)
            self.assertIn("started_at_utc", entry)
            self.assertIn("finished_at_utc", entry)
            self.assertIn("cache_decision", entry)
            self.assertIn("command", entry)

    def test_03_empty_diff_and_non_python_diff_fast_pass(self) -> None:
        """Category: Boundary payload - 0 matching target files fast-passes file-scoped linters and Gates 4/15."""
        # Non-Python diff (e.g. markdown only)
        non_py_targets = ["documentation/architecture.md", "README.md"]
        for g in FILE_SCOPED_PYTHON_GATES:
            self.assertTrue(
                is_empty_scope_skippable(g, non_py_targets, full=False),
                f"Gate {g} should be skippable for non-Python diff",
            )
        self.assertTrue(is_empty_scope_skippable(4, non_py_targets, full=False))
        self.assertTrue(is_empty_scope_skippable(15, non_py_targets, full=False))

        # Full flag must disable empty-scope skipping
        self.assertFalse(is_empty_scope_skippable(4, non_py_targets, full=True))
        self.assertFalse(is_empty_scope_skippable(15, non_py_targets, full=True))

        # Python diff must not skip
        py_targets = ["dual_pipeline/engine.py"]
        self.assertFalse(is_empty_scope_skippable(2, py_targets, full=False))
        self.assertFalse(is_empty_scope_skippable(4, py_targets, full=False))
        self.assertFalse(is_empty_scope_skippable(15, py_targets, full=False))

        # Measure wall clock on simulated 34-stage empty-diff run
        simulated_stages = [
            (idx, f"Gate {idx}", ["python", "-c", "pass"]) for idx in range(1, 35)
        ]
        t0 = time.monotonic()
        results, passed = _execute_unified_stages_ljf(
            stages_slice=simulated_stages,
            total_stages=34,
            use_cache=False,
            max_workers=8,
            head_sha="0000000000000000000000000000000000000000",
            target_files=non_py_targets,
            verbose=False,
            repo_root=ROOT,
            run_stage_fn=lambda name, cmd: (True, "ok", 0.001),
        )
        elapsed = time.monotonic() - t0

        self.assertTrue(passed)
        self.assertLess(elapsed, 0.50, f"Empty-diff run took {elapsed}s (expected < 0.50s)")
        skipped_gates = [r["stage_index"] for r in results if r["cache_decision"] == "empty_scope_skip"]
        self.assertIn(4, skipped_gates)
        self.assertIn(15, skipped_gates)
        for g in FILE_SCOPED_PYTHON_GATES:
            self.assertIn(g, skipped_gates)

    def test_04_gate_31_sandbox_isolation_preserves_root_artifacts(self) -> None:
        """Category: Invariant bypass - Gate 31 uses TemporaryDirectory without wiping repo learning/."""
        with tempfile.TemporaryDirectory(prefix="test_copilot_smoke_") as temp_dir:
            temp_root = Path(temp_dir)
            learning_dir = temp_root / "learning"
            learning_dir.mkdir(parents=True, exist_ok=True)
            sentinel_file = learning_dir / "preserved_learning_ledger.json"
            sentinel_file.write_text('{"records": [1, 2, 3]}', encoding="utf-8")

            local_copilot = temp_root / ".local" / "copilot"
            local_copilot.mkdir(parents=True, exist_ok=True)
            copilot_sentinel = local_copilot / "session_state.json"
            copilot_sentinel.write_text('{"session": "active"}', encoding="utf-8")

            executed_cmds = []

            def dummy_runner(cmd, **kwargs):
                executed_cmds.append(list(cmd))
                return MagicMock(returncode=0, stdout="```text\nActive graph response. Which status is current?\n```\n", stderr="")

            success, msg = verify_copilot_active_graph_smoke(repo_root=temp_root, runner=dummy_runner)
            self.assertTrue(success, f"Gate 31 failed: {msg}")

            # Verify that learning/ and .local/copilot were NOT deleted
            self.assertTrue(sentinel_file.is_file(), "sentinel file in learning/ was deleted by Gate 31!")
            self.assertTrue(copilot_sentinel.is_file(), "copilot sentinel was deleted by Gate 31!")

    def test_05_concurrent_timing_db_atomic_writes(self) -> None:
        """Category: Concurrency & race - Multi-process/thread atomic writes to TestTimingDB do not collide."""
        import concurrent.futures

        with tempfile.TemporaryDirectory(prefix="timing_db_test_") as temp_dir:
            db_file = Path(temp_dir) / "test_timing_db.json"

            def writer_task(worker_id: int):
                for i in range(20):
                    db = TestTimingDB(db_file)
                    db.record(f"test.worker_{worker_id}.case_{i}", 0.1 * (worker_id + 1))
                    db.save()

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
                futures = [ex.submit(writer_task, w) for w in range(8)]
                for f in futures:
                    f.result()

            # Final verify
            final_db = TestTimingDB(db_file)
            self.assertGreater(final_db.entry_count(), 0)
            # Ensure no orphaned temp files remained
            tmp_files = list(Path(temp_dir).glob("*.tmp*"))
            self.assertEqual(len(tmp_files), 0, f"Found lingering temporary files: {tmp_files}")

    def test_06_stage_future_timeout_scales_and_handles_timeout_gracefully(self) -> None:
        """Category: Exact production regression - Stage 29 timeout uses 930s ceiling and catches TimeoutError cleanly."""
        from tools.readiness_concurrent import _stage_future_timeout

        self.assertGreaterEqual(_stage_future_timeout(["python", "tools/run_tests_with_progress.py"]), 900.0)
        self.assertGreaterEqual(_stage_future_timeout(["python", "tools/lint_file_budgets.py"]), 360.0)

        def slow_run_stage(name: str, cmd: list) -> tuple[bool, str, float]:
            time.sleep(0.15)
            return True, "ok", 0.15

        with patch("tools.readiness_concurrent._stage_future_timeout", return_value=0.02):
            results, passed = _execute_unified_stages_ljf(
                stages_slice=[(29, "Core Architectural & Governance Test Suite", ["python", "tools/run_tests_with_progress.py"])],
                total_stages=34,
                use_cache=False,
                max_workers=1,
                head_sha="0000000000000000000000000000000000000000",
                target_files=["foo.py"],
                verbose=False,
                repo_root=ROOT,
                run_stage_fn=slow_run_stage,
            )
        self.assertFalse(passed)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "FAIL")
        self.assertIn("Stage timed out after", results[0]["output_summary"])


if __name__ == "__main__":
    unittest.main()
