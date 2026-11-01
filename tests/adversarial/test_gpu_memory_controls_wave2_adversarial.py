"""Adversarial Acceptance Suite for GPU Memory Capacity & Local Execution Wave 2.

Verifies:
1. Boundary ceiling enforcement: over-allocation on 48 GB, 64 GB, and 128 GB fails closed.
2. Dead worker reclamation: dead PIDs are purged on admission, releasing dead allocations.
3. Invariant protection: living worker allocations with in-flight references block reclamation.
4. Speech worker fault tolerance: worker error receipts raise RuntimeError without silent fallback.
"""
from __future__ import annotations

import os
import queue
from unittest import TestCase

from dual_pipeline.runtime.memory_governor import (
    MemoryGovernor,
    MemoryLimitExceededError,
    ReclamationBlockedError,
)
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
)


class TestGpuMemoryControlsWave2Adversarial(TestCase):
    """Adversarial tests for Wave 2 execution and memory controls."""

    def test_over_allocation_boundary_enforcement(self) -> None:
        """Verifies allocations exceeding hardware limits fail closed across all profiles."""
        gov_48 = MemoryGovernor(total_memory_gb=48.0)
        gov_64 = MemoryGovernor(total_memory_gb=64.0)
        gov_128 = MemoryGovernor(total_memory_gb=128.0)

        # 48 GB envelope: 42.0 GB limit -> 42.1 GB must fail
        with self.assertRaises(MemoryLimitExceededError):
            gov_48.register_allocation("oversized_48", 42.1)

        # 64 GB envelope: 54.0 GB limit -> 54.1 GB must fail
        with self.assertRaises(MemoryLimitExceededError):
            gov_64.register_allocation("oversized_64", 54.1)

        # 128 GB envelope: 112.0 GB limit -> 112.1 GB must fail
        with self.assertRaises(MemoryLimitExceededError):
            gov_128.register_allocation("oversized_128", 112.1)

    def test_dead_worker_auto_reclamation_under_contention(self) -> None:
        """Verifies allocations from dead processes are auto-reclaimed during can_allocate."""
        gov = MemoryGovernor(max_allocatable_gb=20.0)
        dead_pid = 99999999  # Guaranteed non-existent PID

        # Allocate 15 GB to a dead process
        rec = gov.register_allocation("dead_model", 15.0, process_id=dead_pid)
        self.assertEqual(gov.current_allocated_gb, 15.0)

        # Attempt to allocate 10 GB to current process:
        # Total would be 15 + 10 = 25 GB (> 20 GB).
        # can_allocate() MUST auto-reclaim the dead PID allocation, reducing usage to 0 GB,
        # allowing the new 10 GB allocation to succeed!
        can_alloc = gov.can_allocate("new_model", 10.0)
        self.assertTrue(can_alloc)

        # Verify dead allocation was purged
        self.assertNotIn(rec.allocation_id, gov._allocations_by_id)
        new_rec = gov.register_allocation("new_model", 10.0)
        self.assertEqual(gov.current_allocated_gb, 10.0)

    def test_living_worker_in_flight_blocks_reclamation(self) -> None:
        """Verifies in-flight work on an active living process cannot be reclaimed."""
        gov = MemoryGovernor(max_allocatable_gb=20.0)
        current_pid = os.getpid()

        rec = gov.register_allocation("active_model", 8.0, process_id=current_pid)
        gov.acquire_in_flight(rec.allocation_id)

        # Attempting to release an active allocation with in-flight references fails closed
        with self.assertRaises(ReclamationBlockedError):
            gov.release_allocation(rec.allocation_id)

        self.assertEqual(gov.current_allocated_gb, 8.0)

        # Releasing in-flight work enables graceful reclamation
        gov.release_in_flight(rec.allocation_id)
        released = gov.release_allocation(rec.allocation_id)
        self.assertIsNotNone(released)
        self.assertEqual(gov.current_allocated_gb, 0.0)

    def test_transcriber_worker_error_fails_closed_without_fallback(self) -> None:
        """Verifies error receipts from worker raise RuntimeError without silent fallback."""
        client = IsolatedMLXTranscriberClient(mock_mode=False)
        client._output_q = queue.Queue()

        client._output_q.put({
            "type": "RESULT",
            "task_id": "fault_task",
            "status": "ERROR",
            "error": "Simulated Metal GPU memory fault",
        })

        with self.assertRaises(RuntimeError) as ctx:
            client._drain_and_await_result("fault_task", timeout=1.0)

        self.assertIn("Cloud fallback is prohibited", str(ctx.exception))
        client.stop()
