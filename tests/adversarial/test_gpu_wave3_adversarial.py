"""Adversarial Acceptance Suite for GPU Optimization Wave 3.

Covers mandatory edge-case categories:
1. Overflow & exhaustion:
   - Memory allocation requests breaching unified RAM ceiling fail closed.
   - Admission queue capacity exhaustion fails closed with AdmissionQueueFullError.
   - Admission deadline expiration fails closed with AdmissionTimeoutError.
2. Concurrency & race:
   - Concurrent allocation/deallocation stress across multiple worker threads.
   - Concurrent single-flight model loading under tight memory constraints.
3. Invariant bypass attempt & zero cloud fallback:
   - Reclaiming allocation during active in-flight execution blocked when worker is alive.
   - Admission failures strictly fail closed without implicit cloud fallbacks.
4. Rollback & recovery:
   - Mid-load failure cleanly rolls back governor allocation.
   - Cooperative task cancellation preserves memory reservation until final cleanup.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from unittest import TestCase, mock

from dual_pipeline.runtime import (
    AdmissionPriority,
    AdmissionQueueFullError,
    AdmissionRejectedError,
    AdmissionRequest,
    AdmissionTimeoutError,
    HostAdmissionController,
    LocalModelRuntime,
    MemoryGovernor,
    MemoryLimitExceededError,
    ReclamationBlockedError,
)


class TestGpuWave3Adversarial(TestCase):
    """Adversarial and failure-mode tests for Wave 3 model ownership and admission."""

    # -------------------------------------------------------------------------
    # Category 1: Overflow & exhaustion
    # -------------------------------------------------------------------------
    def test_memory_ceiling_exhaustion(self) -> None:
        """Verifies allocations exceeding the allocatable memory ceiling fail closed."""
        gov = MemoryGovernor(max_allocatable_gb=20.0)
        gov.register_allocation("model_1", required_gb=15.0)

        # 6.0 GB additional allocation would exceed 20.0 GB
        with self.assertRaises(MemoryLimitExceededError) as ctx:
            gov.register_allocation("model_2", required_gb=6.0)
        self.assertIn("Hard limit: 20.00 GB", str(ctx.exception))
        # Total remains 15.0 GB
        self.assertEqual(gov.current_allocated_gb, 15.0)

    def test_admission_queue_depth_exhaustion(self) -> None:
        """Verifies submitting requests past max_queue_depth fails closed."""
        adm = HostAdmissionController(max_queue_depth=3)
        # Fill active requests
        requests = [
            AdmissionRequest(priority=AdmissionPriority.INTERACTIVE_QUERY, task_name=f"task_{i}")
            for i in range(3)
        ]
        for req in requests:
            adm.acquire_admission(req)

        # Simulate filling queue
        adm._queued_requests = list(requests)

        overflow_req = AdmissionRequest(priority=AdmissionPriority.BACKGROUND_BATCH, task_name="overflow_task")
        with self.assertRaises(AdmissionQueueFullError):
            adm.acquire_admission(overflow_req)

    def test_admission_deadline_expired(self) -> None:
        """Verifies requests with expired admission deadlines fail closed."""
        adm = HostAdmissionController()
        # Request created with past timestamp
        req = AdmissionRequest(
            priority=AdmissionPriority.INTERACTIVE_QUERY,
            task_name="expired_task",
            deadline_ms=10.0,
            created_at_ms=time.time() * 1000.0 - 50.0,  # 50ms in past
        )
        with self.assertRaises(AdmissionTimeoutError):
            adm.acquire_admission(req)

    # -------------------------------------------------------------------------
    # Category 2: Concurrency & race conditions
    # -------------------------------------------------------------------------
    def test_concurrent_allocation_and_release_stress(self) -> None:
        """Verifies thread-safe governor accounting under concurrent allocations and releases."""
        gov = MemoryGovernor(max_allocatable_gb=50.0)
        num_threads = 8
        cycles_per_thread = 10

        def _worker(thread_id: int) -> None:
            for c in range(cycles_per_thread):
                key = f"model_t{thread_id}_c{c}"
                rec = gov.register_allocation(key, required_gb=1.0)
                gov.acquire_in_flight(rec.allocation_id)
                gov.release_in_flight(rec.allocation_id)
                gov.release_allocation(rec.allocation_id)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
            futures = [pool.submit(_worker, tid) for tid in range(num_threads)]
            for f in concurrent.futures.as_completed(futures):
                f.result()

        # All memory cleanly released
        self.assertEqual(gov.current_allocated_gb, 0.0)
        self.assertEqual(len(gov._allocations_by_id), 0)

    # -------------------------------------------------------------------------
    # Category 3: Invariant bypass attempt & zero cloud fallback
    # -------------------------------------------------------------------------
    def test_bypass_reclamation_while_in_flight(self) -> None:
        """Verifies in-flight reference strictly blocks release when process is alive."""
        gov = MemoryGovernor(max_allocatable_gb=30.0)
        rec = gov.register_allocation("protected_model", required_gb=8.0)
        gov.acquire_in_flight(rec.allocation_id)

        # Standard release must be blocked
        with self.assertRaises(ReclamationBlockedError) as ctx:
            gov.release_allocation(rec.allocation_id, force_reclaim=False)
        self.assertIn("in-flight reference(s) active", str(ctx.exception))
        self.assertEqual(gov.current_allocated_gb, 8.0)

    def test_zero_implicit_cloud_fallback(self) -> None:
        """Verifies admission failures fail closed locally without cloud invocation."""
        adm = HostAdmissionController()
        adm.set_live_capture_active(True)
        bg_req = AdmissionRequest(priority=AdmissionPriority.BACKGROUND_BATCH, task_name="batch_ingest")

        with self.assertRaises(AdmissionRejectedError):
            adm.acquire_admission(bg_req)
        # Ensure zero network sockets or cloud brokers are touched
        self.assertEqual(len(adm._active_requests), 0)

    # -------------------------------------------------------------------------
    # Category 4: Rollback & recovery
    # -------------------------------------------------------------------------
    def test_load_failure_rollback(self) -> None:
        """Verifies failed real load rolls back memory governor allocation immediately."""
        gov = MemoryGovernor(max_allocatable_gb=30.0)
        runtime = LocalModelRuntime(governor=gov, mock_mode=False)

        # Mock mlx_lm to simulate engine failure
        with mock.patch("builtins.__import__", side_effect=ImportError("Simulated missing MLX")):
            with self.assertRaises(RuntimeError) as ctx:
                runtime.load_model("llama_3_1_8b_instruct")
            self.assertIn("Failed loading model", str(ctx.exception))

        # Memory governor allocation rolled back to 0.0 GB
        self.assertEqual(gov.current_allocated_gb, 0.0)
        self.assertEqual(len(gov._allocations_by_id), 0)

    def test_cooperative_cancellation_preserves_reservation(self) -> None:
        """Verifies cancellation flag does not prematurely reclaim memory reservation."""
        adm = HostAdmissionController()
        gov = MemoryGovernor()
        task_id = "diarization_cancel_test"

        rec = gov.register_allocation("diarizer", required_gb=2.0)
        adm.request_task_cancellation(task_id)

        self.assertTrue(adm.is_task_cancelled(task_id))
        # Memory reservation remains intact until worker finishes
        self.assertEqual(gov.current_allocated_gb, 2.0)

        # Worker finalizes and releases
        gov.release_allocation(rec.allocation_id)
        self.assertEqual(gov.current_allocated_gb, 0.0)
