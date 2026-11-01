#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Merge Queue Disjoint Scheduling (Wave 2).

Covers edge case categories:
1. Boundary payload: Empty queue and empty changed_files lists.
2. Malformed input: Invalid, partially written, or corrupted JSON in waiter ledger.
3. Concurrency & race: Multi-threaded concurrent waiter registrations without lock corruption.
4. Invariant bypass prevention: Bypass cap enforcement under rapid repeated polling.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.pr_governance_core.merge_queue import (
    MAX_BYPASS_COUNT,
    QueueItem,
    _read_waiters,
    _write_waiters,
    is_first_live_waiter,
    register_fifo_waiter,
    unregister_fifo_waiter,
)


class MergeQueueAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="mq_adv_")
        self.common_dir = Path(self.tmp_dir) / ".git"
        self.common_dir.mkdir(parents=True)
        self.pid_patcher = patch("tools.pr_governance_core.merge_queue._is_pid_alive", return_value=True)
        self.pid_patcher.start()

    def tearDown(self) -> None:
        self.pid_patcher.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_boundary_payload_empty_queue_and_empty_files(self) -> None:
        """Category: Boundary payload - querying an empty queue or empty files list is safe."""
        # Empty queue should return True for any caller
        can_run = is_first_live_waiter(self.common_dir, "feat/any", 1234)
        self.assertTrue(can_run)

        # Empty changed_files on candidate prevents bypass (cannot prove disjointness)
        register_fifo_waiter(self.common_dir, "feat/head", 1000, changed_files=["a.py"], has_valid_receipt=False)
        register_fifo_waiter(self.common_dir, "feat/cand", 1001, changed_files=[], has_valid_receipt=True)
        can_bypass = is_first_live_waiter(self.common_dir, "feat/cand", 1001)
        self.assertFalse(can_bypass, "Candidate with empty changed_files must not bypass head")

    def test_02_malformed_input_corrupted_waiters_json(self) -> None:
        """Category: Malformed input - corrupted waiters JSON file recovers without crashing."""
        queue_file = self.common_dir / "governed_merge_queue_waiters.json"
        queue_file.write_text("{ NOT_A_LIST: [", encoding="utf-8")

        # Read should safely return empty list without exception
        waiters = _read_waiters(self.common_dir)
        self.assertEqual(waiters, [])

        # Registering new waiter over corrupted file should succeed cleanly
        pos = register_fifo_waiter(self.common_dir, "feat/recovered", 2000)
        self.assertEqual(pos, 1)
        waiters = _read_waiters(self.common_dir)
        self.assertEqual(len(waiters), 1)
        self.assertEqual(waiters[0].branch, "feat/recovered")

    def test_03_concurrency_race_multithreaded_waiters(self) -> None:
        """Category: Concurrency & race - 16 concurrent workers register and unregister safely."""
        num_workers = 16

        def _worker(worker_id: int):
            branch_name = f"feat/worker-{worker_id}"
            pid = 3000 + worker_id
            pos = register_fifo_waiter(
                self.common_dir,
                branch=branch_name,
                pid=pid,
                changed_files=[f"file_{worker_id}.py"],
                has_valid_receipt=True,
            )
            self.assertGreaterEqual(pos, 1)
            unregister_fifo_waiter(self.common_dir, branch_name, pid)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(_worker, i) for i in range(num_workers)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        # All workers unregistered
        final_waiters = _read_waiters(self.common_dir)
        self.assertEqual(len(final_waiters), 0)

    def test_04_invariant_bypass_cap_monotonically_bounded(self) -> None:
        """Category: Invariant bypass - bypass_count never exceeds MAX_BYPASS_COUNT (3)."""
        register_fifo_waiter(self.common_dir, "feat/blocked-head", 4000, changed_files=["hot.py"], has_valid_receipt=False)
        register_fifo_waiter(self.common_dir, "feat/ready-cand", 4001, changed_files=["cold.py"], has_valid_receipt=True)

        for _ in range(10):
            is_first_live_waiter(self.common_dir, "feat/ready-cand", 4001)

        head = [w for w in _read_waiters(self.common_dir) if w.branch == "feat/blocked-head"][0]
        self.assertLessEqual(
            head.bypass_count,
            MAX_BYPASS_COUNT,
            f"bypass_count {head.bypass_count} MUST NEVER exceed MAX_BYPASS_COUNT {MAX_BYPASS_COUNT}",
        )


if __name__ == "__main__":
    unittest.main()
