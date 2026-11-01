"""End-to-End Acceptance Suite for GPU Optimization Wave 3.

Verifies:
1. Process-incarnation and distinct allocation tracking (duplicate keys cannot undercount).
2. Single-flight model loading preventing duplicate memory reservations or thundering herd.
3. Resident and transient budget separation with in-flight reference counting.
4. Reclamation invariant: outstanding work blocks unload unless worker death is confirmed.
5. Host admission priority gating: background batch work is blocked during live capture.
6. Isolated ASR worker request serialization (one request per ASR worker).
7. Strict separation of framework allocations vs host OS memory counters.
8. Wave 3 acceptance runner execution in a disposable sandbox producing sealed receipts.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase, mock

from dual_pipeline.runtime import (
    AdmissionPriority,
    AdmissionRejectedError,
    AdmissionRequest,
    AdmissionTimeoutError,
    HostAdmissionController,
    LocalModelRuntime,
    MemoryGovernor,
    MemoryLimitExceededError,
    ReclamationBlockedError,
)
from tools.gpu_optimization_runner_core import canonical_digest


class TestGpuWave3E2E(TestCase):
    """End-to-end tests for model ownership, admission, and allocation governance."""

    def test_distinct_instance_allocation_and_process_binding(self) -> None:
        """Verifies duplicate model keys across processes create distinct allocations."""
        gov = MemoryGovernor(max_allocatable_gb=40.0)

        # Process 1001 loads model
        r1 = gov.register_allocation(
            model_key="qwen_2_5_14b_instruct",
            required_gb=9.2,
            process_id=1001,
            incarnation_id="inc_1001_a",
            resident_budget_gb=9.2,
            transient_budget_gb=1.0,
        )

        # Process 1002 loads same model
        r2 = gov.register_allocation(
            model_key="qwen_2_5_14b_instruct",
            required_gb=9.2,
            process_id=1002,
            incarnation_id="inc_1002_b",
            resident_budget_gb=9.2,
            transient_budget_gb=1.0,
        )

        self.assertNotEqual(r1.allocation_id, r2.allocation_id)
        self.assertNotEqual(r1.process_id, r2.process_id)
        # Total memory must account for both physical allocations (10.2 + 10.2 = 20.4 GB)
        self.assertAlmostEqual(gov.current_allocated_gb, 20.4, places=2)

    def test_single_flight_model_loading(self) -> None:
        """Verifies concurrent load requests for the same model execute single-flight."""
        gov = MemoryGovernor(max_allocatable_gb=30.0)
        runtime = LocalModelRuntime(governor=gov, mock_mode=True)

        load_barrier = threading.Barrier(4)
        results: list[bool] = []

        def _concurrent_loader() -> None:
            load_barrier.wait()
            ok = runtime.load_model("llama_3_1_8b_instruct")
            results.append(ok)

        threads = [threading.Thread(target=_concurrent_loader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 4)
        self.assertTrue(all(results))
        # Exactly one allocation for this runtime instance
        allocs = [r for r in gov._allocations_by_id.values() if r.model_key == "llama_3_1_8b_instruct"]
        self.assertEqual(len(allocs), 1)

    def test_in_flight_reference_tracking_and_reclamation_blocked(self) -> None:
        """Verifies in-flight work blocks unload until generation finishes."""
        gov = MemoryGovernor(max_allocatable_gb=30.0)
        runtime = LocalModelRuntime(governor=gov, mock_mode=True)
        model_key = "llama_3_1_8b_instruct"

        runtime.load_model(model_key)
        # Simulate active in-flight request
        gov.acquire_in_flight(model_key)

        # Unload must fail closed with ReclamationBlockedError
        with self.assertRaises(ReclamationBlockedError):
            runtime.unload_model(model_key)

        # After in-flight work completes, unload succeeds cleanly
        gov.release_in_flight(model_key)
        unloaded = runtime.unload_model(model_key)
        self.assertTrue(unloaded)
        self.assertNotIn(model_key, runtime._loaded_models)

    def test_reclamation_permitted_on_confirmed_worker_death(self) -> None:
        """Verifies orphaned allocations can be reclaimed if the owning process is dead."""
        gov = MemoryGovernor(max_allocatable_gb=30.0)
        dead_pid = 99999999  # Guaranteed non-existent PID

        rec = gov.register_allocation(
            model_key="dead_worker_model",
            required_gb=5.0,
            process_id=dead_pid,
        )
        gov.acquire_in_flight(rec.allocation_id)

        # Process is dead, so release_allocation must successfully reclaim orphaned reservation
        released = gov.release_allocation(rec.allocation_id)
        self.assertIsNotNone(released)
        self.assertEqual(released.allocation_id, rec.allocation_id)
        self.assertEqual(gov.current_allocated_gb, 0.0)

    def test_host_admission_live_capture_protection(self) -> None:
        """Verifies background batch work is rejected during active live speech capture."""
        adm = HostAdmissionController(max_queue_depth=8)
        adm.set_live_capture_active(True)

        live_req = AdmissionRequest(
            priority=AdmissionPriority.LIVE_TRANSCRIPTION,
            task_name="streaming_mic_transcription",
        )
        bg_req = AdmissionRequest(
            priority=AdmissionPriority.BACKGROUND_BATCH,
            task_name="bulk_vector_indexing",
        )

        # Live capture must be admitted
        self.assertTrue(adm.acquire_admission(live_req))

        # Background batch must be rejected
        with self.assertRaises(AdmissionRejectedError):
            adm.acquire_admission(bg_req)

        # Deactivating live capture allows background work to proceed
        adm.set_live_capture_active(False)
        self.assertTrue(adm.acquire_admission(bg_req))

        adm.release_admission(live_req)
        adm.release_admission(bg_req)

    def test_asr_worker_request_serialization(self) -> None:
        """Verifies exactly one transcription request runs per ASR worker at any time."""
        adm = HostAdmissionController()
        worker_id = "asr_worker_metal_01"
        order: list[str] = []

        def _task_one() -> None:
            with adm.serialize_asr_request(worker_id, timeout_seconds=5.0):
                order.append("task_1_start")
                time.sleep(0.05)
                order.append("task_1_end")

        def _task_two() -> None:
            time.sleep(0.01)  # Ensure task 1 starts first
            with adm.serialize_asr_request(worker_id, timeout_seconds=5.0):
                order.append("task_2_start")
                time.sleep(0.02)
                order.append("task_2_end")

        t1 = threading.Thread(target=_task_one)
        t2 = threading.Thread(target=_task_two)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Tasks must execute serially without overlapping
        self.assertEqual(order, ["task_1_start", "task_1_end", "task_2_start", "task_2_end"])

    def test_framework_vs_host_counters_separation(self) -> None:
        """Verifies framework model allocations and OS host counters are strictly separated."""
        gov = MemoryGovernor()
        gov.register_allocation("test_model", required_gb=4.5)

        status = gov.get_status()
        self.assertTrue(status["counters_strictly_separated"])
        self.assertIn("framework_memory", status)
        self.assertIn("host_memory", status)

        self.assertEqual(status["framework_memory"]["current_allocated_gb"], 4.5)
        self.assertGreater(status["host_memory"]["total_system_memory_gb"], 0.0)
        self.assertGreater(status["host_memory"]["host_memory_pressure_pct"], 0.0)

    def test_wave3_runner_sandbox_execution(self) -> None:
        """Verifies execute_gpu_wave creates a sealed receipt for Wave 3 in disposable sandbox."""
        from tools.run_gpu_optimization_wave import execute_gpu_wave

        with tempfile.TemporaryDirectory() as sandbox_dir:
            sandbox = Path(sandbox_dir)
            receipt_dir = sandbox / "receipts"
            plan_path = Path(__file__).resolve().parents[2] / "docs/plans/docs_gpu_optimization_debate_plan_20261006.md"

            receipt_dir.mkdir(parents=True, exist_ok=True)
            w2_receipt_src = Path(__file__).resolve().parents[2] / "reports/gpu_optimization/gpu_wave_2_receipt.json"
            if w2_receipt_src.is_file():
                w2_target = receipt_dir / "gpu_wave_2_receipt.json"
                w2_target.write_text(w2_receipt_src.read_text(encoding="utf-8"), encoding="utf-8")

            mock_run_res = mock.MagicMock()
            mock_run_res.total_tests = 14
            mock_run_res.passed_count = 14
            mock_run_res.failed_count = 0
            mock_run_res.error_count = 0
            mock_run_res.skipped_count = 0
            mock_run_res.elapsed_seconds = 1.65
            mock_run_res.collected_ids = [f"test_gpu_w3_{i}" for i in range(14)]
            mock_run_res.wasSuccessful.return_value = True

            with mock.patch("tools.run_gpu_optimization_wave.run_pytest_with_progress", return_value=mock_run_res):
                exit_code = execute_gpu_wave(
                    wave=3,
                    sandbox=sandbox,
                    plan_path=plan_path,
                    receipt_root=receipt_dir,
                    timeout=60,
                    immutable=True,
                )
            self.assertEqual(exit_code, 0)

            receipt_file = receipt_dir / "gpu_wave_3_receipt.json"
            self.assertTrue(receipt_file.is_file())
            receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))
            self.assertEqual(receipt_data["status"], "PASS")
            self.assertEqual(receipt_data["wave"], 3)
            self.assertIn("receipt_digest", receipt_data)
            self.assertIn("parent_receipt", receipt_data)
            self.assertEqual(receipt_data["parent_receipt"]["wave"], 2)
